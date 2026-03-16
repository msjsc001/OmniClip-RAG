import shutil
import unittest
from pathlib import Path

import omniclip_rag  # noqa: F401

from omniclip_rag.config import AppConfig, ensure_data_paths
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
