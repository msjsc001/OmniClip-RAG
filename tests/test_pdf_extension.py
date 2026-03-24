import shutil
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import omniclip_rag  # noqa: F401

from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.extensions import service as extension_service
from omniclip_rag.extensions.build_state import read_extension_build_state
from omniclip_rag.extensions.models import ExtensionDirectoryState, ExtensionIndexState, ExtensionSourceDirectory
from omniclip_rag.extensions.normalizers.pdf import normalize_pdf_pages
from omniclip_rag.extensions.registry import ExtensionRegistry, ExtensionRegistryState
from omniclip_rag.extensions.service import PdfExtensionService
from omniclip_rag.extensions.paths import build_extension_data_paths
from omniclip_rag.extensions.parsers.pdf import parse_pdf_file
from omniclip_rag.service import OmniClipService
from omniclip_rag.storage import MetadataStore

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / '.tmp' / 'test_pdf_extension'


class PdfExtensionTests(unittest.TestCase):
    def setUp(self) -> None:
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def tearDown(self) -> None:
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def test_normalize_pdf_pages_merges_soft_wrapped_lines(self) -> None:
        rows = normalize_pdf_pages(
            [
                {
                    'page_no': 1,
                    'text': 'This line has no period\nand continues here.\n\nNew paragraph starts.',
                }
            ]
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['page_no'], 1)
        self.assertEqual(rows[0]['text'], 'This line has no period and continues here.')
        self.assertEqual(rows[1]['text'], 'New paragraph starts.')

    def test_parse_pdf_file_extracts_page_label_and_stitched_paragraphs(self) -> None:
        source_root = TEST_ROOT / 'pdf_source'
        source_root.mkdir(parents=True, exist_ok=True)
        pdf_path = source_root / 'guide.pdf'
        write_text_pdf(
            pdf_path,
            [
                'This line has no period',
                'and continues here.',
                'New paragraph starts.',
            ],
        )

        parsed = parse_pdf_file(source_root, pdf_path)

        self.assertEqual(parsed.kind, 'pdf')
        self.assertEqual(parsed.title, 'guide.pdf')
        self.assertGreaterEqual(len(parsed.chunks), 2)
        self.assertEqual(parsed.chunks[0].anchor, '第 1 页')
        self.assertIn('This line has no period and continues here.', [chunk.raw_text for chunk in parsed.chunks])

    def test_pdf_full_rebuild_skips_broken_files_and_uses_isolated_storage(self) -> None:
        vault = TEST_ROOT / 'vault'
        vault.mkdir(parents=True, exist_ok=True)
        (vault / 'note.md').write_text('# Main\n\nMarkdown should stay isolated.', encoding='utf-8')
        pdf_root = TEST_ROOT / 'pdf_source'
        pdf_root.mkdir(parents=True, exist_ok=True)
        write_text_pdf(pdf_root / 'guide.pdf', ['PdfUniqueToken', 'continues here.'])
        (pdf_root / 'broken.pdf').write_bytes(b'not-a-real-pdf')

        paths = ensure_data_paths(str(TEST_ROOT / 'data'), str(vault))
        config = AppConfig(vault_path=str(vault), data_root=str(paths.global_root), vector_backend='disabled', reranker_enabled=False)
        state = ExtensionRegistryState()
        state.pdf_config.enabled = True
        state.pdf_config.source_directories = [
            ExtensionSourceDirectory(
                path=str(pdf_root),
                selected=True,
                state=ExtensionDirectoryState.ENABLED,
            )
        ]
        ExtensionRegistry().save(paths, state)

        service = PdfExtensionService(config, paths)
        try:
            report = service.full_rebuild()
        finally:
            service.close()

        self.assertEqual(report.indexed_files, 1)
        self.assertEqual(report.skipped_files, 1)
        isolated_paths = build_extension_data_paths(paths, 'pdf')
        self.assertTrue(isolated_paths.sqlite_file.exists())
        self.assertIn('extensions', str(isolated_paths.sqlite_file))

        main_store = MetadataStore(paths.sqlite_file)
        try:
            self.assertEqual(main_store.stats()['files'], 0)
        finally:
            main_store.close()

        reloaded = ExtensionRegistry().load(paths)
        self.assertEqual(reloaded.snapshot.pdf.index_state, ExtensionIndexState.READY)
        self.assertEqual(reloaded.snapshot.pdf.indexed_document_count, 1)

    def test_pdf_preflight_tolerates_missing_pypdf_metadata_when_module_is_bundled(self) -> None:
        vault = TEST_ROOT / 'vault_preflight'
        vault.mkdir(parents=True, exist_ok=True)
        pdf_root = TEST_ROOT / 'pdf_preflight'
        pdf_root.mkdir(parents=True, exist_ok=True)
        write_text_pdf(pdf_root / 'guide.pdf', ['Pdf preflight token'])

        paths = ensure_data_paths(str(TEST_ROOT / 'data_preflight'), str(vault))
        config = AppConfig(vault_path=str(vault), data_root=str(paths.global_root), vector_backend='disabled', reranker_enabled=False)
        state = ExtensionRegistryState()
        state.pdf_config.enabled = True
        state.pdf_config.source_directories = [
            ExtensionSourceDirectory(path=str(pdf_root), selected=True, state=ExtensionDirectoryState.ENABLED)
        ]
        ExtensionRegistry().save(paths, state)

        service = PdfExtensionService(config, paths)
        try:
            with patch(
                'omniclip_rag.extensions.service.importlib_metadata.version',
                side_effect=extension_service.importlib_metadata.PackageNotFoundError('pypdf'),
            ):
                report = service.preflight()
        finally:
            service.close()

        self.assertEqual(report.total_files, 1)
        self.assertEqual(report.skipped_files, 0)
        self.assertGreater(report.total_bytes, 0)

    def test_pdf_preflight_emits_inspection_progress_without_extracting_text(self) -> None:
        vault = TEST_ROOT / 'vault_preflight_progress'
        vault.mkdir(parents=True, exist_ok=True)
        pdf_root = TEST_ROOT / 'pdf_preflight_progress'
        pdf_root.mkdir(parents=True, exist_ok=True)
        write_text_pdf(pdf_root / 'guide.pdf', ['Pdf preflight progress token'])

        paths = ensure_data_paths(str(TEST_ROOT / 'data_preflight_progress'), str(vault))
        config = AppConfig(vault_path=str(vault), data_root=str(paths.global_root), vector_backend='disabled', reranker_enabled=False)
        state = ExtensionRegistryState()
        state.pdf_config.enabled = True
        state.pdf_config.source_directories = [
            ExtensionSourceDirectory(path=str(pdf_root), selected=True, state=ExtensionDirectoryState.ENABLED)
        ]
        ExtensionRegistry().save(paths, state)

        progress_events: list[dict[str, object]] = []
        service = PdfExtensionService(config, paths)
        try:
            with patch('omniclip_rag.extensions.service._extract_pdf_pages', side_effect=AssertionError('preflight must stay lightweight')):
                report = service.preflight(on_progress=progress_events.append)
        finally:
            service.close()

        self.assertEqual(report.total_files, 1)
        self.assertEqual(report.total_pages, 1)
        stage_sequence = [str(item.get('stage_status') or '') for item in progress_events]
        self.assertIn('scan_sources', stage_sequence)
        self.assertIn('inspect_pdf', stage_sequence)
        self.assertIn('finalizing', stage_sequence)

    def test_main_query_broker_returns_pdf_hits_with_page_identity(self) -> None:
        vault = TEST_ROOT / 'vault_query'
        vault.mkdir(parents=True, exist_ok=True)
        (vault / 'note.md').write_text('# Main\n\nMarkdown content unrelated to the PDF token.', encoding='utf-8')
        pdf_root = TEST_ROOT / 'pdf_query'
        pdf_root.mkdir(parents=True, exist_ok=True)
        write_text_pdf(pdf_root / 'guide.pdf', ['PhaseThreePdfMagicToken', 'and supporting context.'])

        paths = ensure_data_paths(str(TEST_ROOT / 'data_query'), str(vault))
        config = AppConfig(vault_path=str(vault), data_root=str(paths.global_root), vector_backend='disabled', reranker_enabled=False)

        registry_state = ExtensionRegistryState()
        registry_state.pdf_config.enabled = True
        registry_state.pdf_config.source_directories = [
            ExtensionSourceDirectory(
                path=str(pdf_root),
                selected=True,
                state=ExtensionDirectoryState.ENABLED,
            )
        ]
        ExtensionRegistry().save(paths, registry_state)

        pdf_service = PdfExtensionService(config, paths)
        try:
            pdf_service.full_rebuild()
        finally:
            pdf_service.close()

        main_service = OmniClipService(config, paths)
        try:
            main_service.rebuild_index()
            result = main_service.query('PhaseThreePdfMagicToken', limit=5, score_threshold=0)
        finally:
            main_service.close()

        pdf_hits = [hit for hit in result.hits if hit.source_family == 'pdf']
        self.assertTrue(pdf_hits)
        self.assertEqual(pdf_hits[0].page_no, 1)
        self.assertEqual(pdf_hits[0].source_kind, 'pdf')
        self.assertIn('PDF · guide.pdf · 第 1 页', pdf_hits[0].title)
        self.assertEqual(pdf_hits[0].source_label, 'PDF · guide.pdf · 第 1 页')
        self.assertEqual(pdf_hits[0].anchor, '第 1 页')

    def test_pdf_full_rebuild_cancel_marks_resumable_state(self) -> None:
        vault = TEST_ROOT / 'vault_cancel'
        vault.mkdir(parents=True, exist_ok=True)
        pdf_root = TEST_ROOT / 'pdf_cancel'
        pdf_root.mkdir(parents=True, exist_ok=True)
        write_text_pdf(pdf_root / 'guide.pdf', ['Pdf cancel token'])

        paths = ensure_data_paths(str(TEST_ROOT / 'data_cancel'), str(vault))
        config = AppConfig(vault_path=str(vault), data_root=str(paths.global_root), vector_backend='disabled', reranker_enabled=False)
        state = ExtensionRegistryState()
        state.pdf_config.enabled = True
        state.pdf_config.source_directories = [
            ExtensionSourceDirectory(path=str(pdf_root), selected=True, state=ExtensionDirectoryState.ENABLED)
        ]
        ExtensionRegistry().save(paths, state)

        cancel_event = threading.Event()
        cancel_event.set()
        service = PdfExtensionService(config, paths)
        try:
            report = service.full_rebuild(cancel_event=cancel_event)
        finally:
            service.close()

        self.assertTrue(report.cancelled)
        self.assertTrue(report.resume_available)
        reloaded = ExtensionRegistry().load(paths)
        self.assertEqual(reloaded.snapshot.pdf.index_state, ExtensionIndexState.RESUMABLE)
        build_state = read_extension_build_state(build_extension_data_paths(paths, 'pdf'))
        self.assertIsInstance(build_state, dict)
        self.assertEqual(str(build_state.get('status') or ''), 'resumable')

    def test_pdf_full_rebuild_runtime_failure_marks_resumable_state(self) -> None:
        vault = TEST_ROOT / 'vault_failure'
        vault.mkdir(parents=True, exist_ok=True)
        pdf_root = TEST_ROOT / 'pdf_failure'
        pdf_root.mkdir(parents=True, exist_ok=True)
        write_text_pdf(pdf_root / 'guide.pdf', ['Pdf fatal token'])

        paths = ensure_data_paths(str(TEST_ROOT / 'data_failure'), str(vault))
        config = AppConfig(vault_path=str(vault), data_root=str(paths.global_root), vector_backend='disabled', reranker_enabled=False)
        state = ExtensionRegistryState()
        state.pdf_config.enabled = True
        state.pdf_config.source_directories = [
            ExtensionSourceDirectory(path=str(pdf_root), selected=True, state=ExtensionDirectoryState.ENABLED)
        ]
        ExtensionRegistry().save(paths, state)

        progress_events: list[dict[str, object]] = []
        service = PdfExtensionService(config, paths)
        try:
            with patch.object(service, '_replace_one_pdf', side_effect=RuntimeError('pdf_fatal_failure')):
                with self.assertRaisesRegex(RuntimeError, 'pdf_fatal_failure'):
                    service.full_rebuild(on_progress=progress_events.append)
        finally:
            service.close()

        reloaded = ExtensionRegistry().load(paths)
        self.assertEqual(reloaded.snapshot.pdf.index_state, ExtensionIndexState.RESUMABLE)
        self.assertTrue(reloaded.snapshot.pdf.resume_available)
        self.assertFalse(reloaded.snapshot.pdf.query_ready)
        build_state = read_extension_build_state(build_extension_data_paths(paths, 'pdf'))
        self.assertIsInstance(build_state, dict)
        self.assertEqual(str(build_state.get('status') or ''), 'resumable')
        self.assertIn('pdf_fatal_failure', str(build_state.get('last_error') or ''))
        self.assertTrue(progress_events)

    def test_pdf_build_watchdog_writes_diagnostic_report_when_parse_stalls(self) -> None:
        vault = TEST_ROOT / 'vault_watchdog'
        vault.mkdir(parents=True, exist_ok=True)
        pdf_root = TEST_ROOT / 'pdf_watchdog'
        pdf_root.mkdir(parents=True, exist_ok=True)
        write_text_pdf(pdf_root / 'guide.pdf', ['Pdf watchdog token'])

        paths = ensure_data_paths(str(TEST_ROOT / 'data_watchdog'), str(vault))
        config = AppConfig(vault_path=str(vault), data_root=str(paths.global_root), vector_backend='disabled', reranker_enabled=False)
        state = ExtensionRegistryState()
        state.pdf_config.enabled = True
        state.pdf_config.source_directories = [
            ExtensionSourceDirectory(path=str(pdf_root), selected=True, state=ExtensionDirectoryState.ENABLED)
        ]
        ExtensionRegistry().save(paths, state)

        progress_events: list[dict[str, object]] = []
        service = PdfExtensionService(config, paths)
        original_replace = service._replace_one_pdf

        def slow_replace(source_root: Path, pdf_path: Path):
            time.sleep(0.12)
            return original_replace(source_root, pdf_path)

        try:
            with patch('omniclip_rag.extensions.service.EXTENSION_BUILD_HEARTBEAT_SECONDS', 0.02), \
                patch('omniclip_rag.extensions.service.EXTENSION_BUILD_WATCHDOG_STALL_SECONDS', 0.05), \
                patch('omniclip_rag.extensions.service.EXTENSION_BUILD_WATCHDOG_REPEAT_SECONDS', 0.05), \
                patch.object(service, '_replace_one_pdf', side_effect=slow_replace):
                report = service.full_rebuild(on_progress=progress_events.append)
        finally:
            service.close()

        self.assertGreaterEqual(report.indexed_files, 1)
        self.assertTrue(any(bool(item.get('watchdog_stalled')) for item in progress_events))
        diagnostics_dir = build_extension_data_paths(paths, 'pdf').logs_dir / 'diagnostics'
        diagnostics = sorted(diagnostics_dir.glob('pdf-build-watchdog-*.json'))
        self.assertTrue(diagnostics)


def write_text_pdf(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_text_pdf(lines))


def build_text_pdf(lines: list[str]) -> bytes:
    y = 760
    operations: list[str] = []
    for line in lines:
        safe = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        operations.append(f'BT /F1 12 Tf 72 {y} Td ({safe}) Tj ET')
        y -= 18
    stream = '\n'.join(operations).encode('latin-1')
    objects = [
        b'1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n',
        b'2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n',
        b'3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n',
        b'4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n',
        f'5 0 obj << /Length {len(stream)} >> stream\n'.encode('ascii') + stream + b'\nendstream endobj\n',
    ]
    parts = [b'%PDF-1.4\n']
    offsets = [0]
    current = len(parts[0])
    for obj in objects:
        offsets.append(current)
        parts.append(obj)
        current += len(obj)
    xref_start = current
    xref = [f'xref\n0 {len(offsets)}\n'.encode('ascii'), b'0000000000 65535 f \n']
    for offset in offsets[1:]:
        xref.append(f'{offset:010d} 00000 n \n'.encode('ascii'))
    trailer = f'trailer << /Root 1 0 R /Size {len(offsets)} >>\nstartxref\n{xref_start}\n%%EOF\n'.encode('ascii')
    return b''.join(parts + xref + [trailer])


if __name__ == '__main__':
    unittest.main()
