import shutil
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import omniclip_rag  # noqa: F401

from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.extensions.models import ExtensionDirectoryState, ExtensionIndexState, ExtensionSourceDirectory, TikaRuntimeStatus
from omniclip_rag.extensions.normalizers.tika_output import normalize_tika_xhtml
from omniclip_rag.extensions.registry import ExtensionRegistry, ExtensionRegistryState
from omniclip_rag.extensions.service import TikaExtensionService
from omniclip_rag.service import OmniClipService
from omniclip_rag.extensions.watch import ExtensionWatchService
from omniclip_rag.extensions.paths import build_extension_data_paths
from omniclip_rag.storage import MetadataStore

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / '.tmp' / 'test_tika_extension'


class _FakeTikaRuntimeManager:
    def __init__(self, port: int = 9998) -> None:
        self.port = port
        self.ensure_started_calls = 0

    def ensure_started(self, _paths):
        self.ensure_started_calls += 1
        return TikaRuntimeStatus(installed=True, java_available=True, jar_available=True, running=True, healthy=True, version='3.2.3', port=self.port)

    def status(self, _paths):
        return TikaRuntimeStatus(installed=True, java_available=True, jar_available=True, running=True, healthy=True, version='3.2.3', port=self.port)


class _FakeScanService:
    def __init__(self) -> None:
        self.scan_calls = 0

    def scan_once(self, **_kwargs):
        self.scan_calls += 1

    def close(self):
        return None


class TikaExtensionTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def test_normalize_tika_xhtml_extracts_semantic_paragraphs(self) -> None:
        xhtml = '<html xmlns="http://www.w3.org/1999/xhtml"><body><div><h1>Guide</h1><p>First paragraph.</p><p>Second paragraph.</p></div><div>Loose block text.</div></body></html>'
        rows = normalize_tika_xhtml(xhtml)
        self.assertEqual([row['text'] for row in rows], ['First paragraph.', 'Second paragraph.', 'Loose block text.'])
        self.assertEqual(rows[0]['anchor'], 'Guide')

    def test_tika_full_rebuild_uses_isolated_storage_and_skips_poisoned_files(self) -> None:
        vault = TEST_ROOT / 'vault'
        vault.mkdir(parents=True, exist_ok=True)
        (vault / 'note.md').write_text('# Main\n\nMarkdown should stay isolated.', encoding='utf-8')
        source_root = TEST_ROOT / 'tika_source'
        source_root.mkdir(parents=True, exist_ok=True)
        (source_root / 'guide.html').write_text('<html/>', encoding='utf-8')
        (source_root / 'slides.docx').write_bytes(b'docx')
        (source_root / 'poisoned.html').write_text('<html/>', encoding='utf-8')

        paths = ensure_data_paths(str(TEST_ROOT / 'data'), str(vault))
        config = AppConfig(vault_path=str(vault), data_root=str(paths.global_root), vector_backend='disabled', reranker_enabled=False)
        state = ExtensionRegistryState()
        state.tika_config.enabled = True
        state.tika_config.source_directories = [ExtensionSourceDirectory(path=str(source_root), selected=True, state=ExtensionDirectoryState.ENABLED)]
        for item in state.tika_config.selected_formats:
            item.enabled = item.format_id in {'html', 'docx'}
        ExtensionRegistry().save(paths, state)

        runtime_manager = _FakeTikaRuntimeManager()
        service = TikaExtensionService(config, paths, runtime_manager=runtime_manager)
        try:
            with patch('omniclip_rag.extensions.service.parse_file_with_tika', side_effect=self._fake_tika_parse):
                report = service.full_rebuild()
        finally:
            service.close()

        self.assertEqual(report.indexed_files, 2)
        self.assertEqual(report.skipped_files, 1)
        self.assertEqual(set(report.enabled_formats), {'html', 'docx'})
        isolated_paths = build_extension_data_paths(paths, 'tika')
        self.assertTrue(isolated_paths.sqlite_file.exists())
        self.assertIn('extensions', str(isolated_paths.sqlite_file))

        main_store = MetadataStore(paths.sqlite_file)
        try:
            self.assertEqual(main_store.stats()['files'], 0)
        finally:
            main_store.close()

        reloaded = ExtensionRegistry().load(paths)
        self.assertEqual(reloaded.snapshot.tika.index_state, ExtensionIndexState.READY)
        self.assertEqual(reloaded.snapshot.tika.indexed_document_count, 2)
        self.assertTrue(reloaded.snapshot.tika.runtime.running)
        self.assertEqual(runtime_manager.ensure_started_calls, 1)

    def test_tika_scan_once_updates_changed_and_removed_files(self) -> None:
        vault = TEST_ROOT / 'vault_scan'
        vault.mkdir(parents=True, exist_ok=True)
        source_root = TEST_ROOT / 'tika_scan_source'
        source_root.mkdir(parents=True, exist_ok=True)
        html_path = source_root / 'guide.html'
        docx_path = source_root / 'slides.docx'
        html_path.write_text('v1', encoding='utf-8')
        docx_path.write_bytes(b'docx-v1')

        paths = ensure_data_paths(str(TEST_ROOT / 'data_scan'), str(vault))
        config = AppConfig(vault_path=str(vault), data_root=str(paths.global_root), vector_backend='disabled', reranker_enabled=False)
        state = ExtensionRegistryState()
        state.tika_config.enabled = True
        state.tika_config.source_directories = [ExtensionSourceDirectory(path=str(source_root), selected=True, state=ExtensionDirectoryState.ENABLED)]
        for item in state.tika_config.selected_formats:
            item.enabled = item.format_id in {'html', 'docx'}
        ExtensionRegistry().save(paths, state)

        runtime_manager = _FakeTikaRuntimeManager()
        service = TikaExtensionService(config, paths, runtime_manager=runtime_manager)
        try:
            with patch('omniclip_rag.extensions.service.parse_file_with_tika', side_effect=self._fake_tika_parse):
                service.full_rebuild()
            time.sleep(0.02)
            html_path.write_text('v2', encoding='utf-8')
            docx_path.unlink()
            with patch('omniclip_rag.extensions.service.parse_file_with_tika', side_effect=self._fake_tika_parse):
                report = service.scan_once()
            manifest = service.store.fetch_file_manifest()
        finally:
            service.close()

        self.assertEqual(report.deleted_files, 1)
        self.assertEqual(report.indexed_files, 1)
        self.assertIn(str(html_path.resolve()), manifest)
        self.assertNotIn(str(docx_path.resolve()), manifest)

    def test_main_query_can_return_tika_only_hits_with_subtype_identity(self) -> None:
        vault = TEST_ROOT / 'vault_query'
        vault.mkdir(parents=True, exist_ok=True)
        source_root = TEST_ROOT / 'tika_query_source'
        source_root.mkdir(parents=True, exist_ok=True)
        (source_root / 'guide.docx').write_bytes(b'docx')

        paths = ensure_data_paths(str(TEST_ROOT / 'data_query'), str(vault))
        config = AppConfig(vault_path=str(vault), data_root=str(paths.global_root), vector_backend='disabled', reranker_enabled=False)
        state = ExtensionRegistryState()
        state.tika_config.enabled = True
        state.tika_config.source_directories = [ExtensionSourceDirectory(path=str(source_root), selected=True, state=ExtensionDirectoryState.ENABLED)]
        for item in state.tika_config.selected_formats:
            item.enabled = item.format_id == 'docx'
        ExtensionRegistry().save(paths, state)

        runtime_manager = _FakeTikaRuntimeManager()
        build_service = TikaExtensionService(config, paths, runtime_manager=runtime_manager)
        try:
            with patch('omniclip_rag.extensions.service.parse_file_with_tika', side_effect=self._fake_tika_parse):
                build_service.full_rebuild()
        finally:
            build_service.close()

        main_service = OmniClipService(config, paths)
        try:
            result = main_service.query('Docx Magic Paragraph', limit=5, score_threshold=0, allowed_families={'tika'})
        finally:
            main_service.close()

        self.assertTrue(result.hits)
        self.assertTrue(all(hit.source_family == 'tika' for hit in result.hits))
        self.assertEqual(result.hits[0].source_kind, 'docx')
        self.assertIn('DOCX(Tika) · guide.docx', result.hits[0].source_label)

    def test_tika_watch_service_blocks_when_markdown_rebuild_active(self) -> None:
        vault = TEST_ROOT / 'vault_watch_block'
        source_root = TEST_ROOT / 'watch_block_source'
        source_root.mkdir(parents=True, exist_ok=True)
        paths = ensure_data_paths(str(TEST_ROOT / 'data_watch_block'), str(vault))
        config = AppConfig(vault_path=str(vault), data_root=str(paths.global_root))
        state = ExtensionRegistryState()
        state.tika_config.enabled = True
        state.tika_config.source_directories = [ExtensionSourceDirectory(path=str(source_root), selected=True, state=ExtensionDirectoryState.ENABLED)]
        for item in state.tika_config.selected_formats:
            item.enabled = item.format_id == 'html'
        ExtensionRegistry().save(paths, state)

        fake_scan = _FakeScanService()
        watch = ExtensionWatchService(
            config,
            paths,
            markdown_rebuild_active=lambda: True,
            tika_service_factory=lambda: fake_scan,
            poll_interval=0.1,
        )
        try:
            watch.start_tika_watch()
            time.sleep(0.15)
            (source_root / 'guide.html').write_text('hello', encoding='utf-8')
            time.sleep(0.6)
        finally:
            watch.stop_tika_watch()

        state = ExtensionRegistry().load(paths)
        self.assertGreater(state.snapshot.tika.watch_state.pending_changes, 0)
        self.assertEqual(fake_scan.scan_calls, 0)
        self.assertFalse(state.snapshot.tika.watch_running)

    def test_tika_watch_service_runs_scan_when_idle(self) -> None:
        vault = TEST_ROOT / 'vault_watch_idle'
        source_root = TEST_ROOT / 'watch_idle_source'
        source_root.mkdir(parents=True, exist_ok=True)
        paths = ensure_data_paths(str(TEST_ROOT / 'data_watch_idle'), str(vault))
        config = AppConfig(vault_path=str(vault), data_root=str(paths.global_root))
        state = ExtensionRegistryState()
        state.tika_config.enabled = True
        state.tika_config.source_directories = [ExtensionSourceDirectory(path=str(source_root), selected=True, state=ExtensionDirectoryState.ENABLED)]
        for item in state.tika_config.selected_formats:
            item.enabled = item.format_id == 'html'
        ExtensionRegistry().save(paths, state)

        fake_scan = _FakeScanService()
        watch = ExtensionWatchService(
            config,
            paths,
            markdown_rebuild_active=lambda: False,
            tika_service_factory=lambda: fake_scan,
            poll_interval=0.1,
        )
        try:
            watch.start_tika_watch()
            time.sleep(0.15)
            (source_root / 'guide.html').write_text('hello', encoding='utf-8')
            time.sleep(0.6)
        finally:
            watch.stop_tika_watch()

        state = ExtensionRegistry().load(paths)
        self.assertGreaterEqual(fake_scan.scan_calls, 1)
        self.assertTrue(bool(state.snapshot.tika.watch_state.last_scan_at))
        self.assertFalse(state.snapshot.tika.watch_running)

    def _fake_tika_parse(self, file_path: Path, *, port: int = 9998, timeout: float = 60.0) -> str:
        del port, timeout
        name = Path(file_path).name.lower()
        if name == 'poisoned.html':
            raise RuntimeError('poisoned_document')
        if name.endswith('.docx'):
            return '<html xmlns="http://www.w3.org/1999/xhtml"><body><div><p>Docx Magic Paragraph.</p></div></body></html>'
        return '<html xmlns="http://www.w3.org/1999/xhtml"><body><div><h1>Guide</h1><p>HTML Magic Paragraph.</p></div></body></html>'


if __name__ == '__main__':
    unittest.main()
