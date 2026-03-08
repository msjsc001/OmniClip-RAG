from pathlib import Path
import shutil
import threading
import time
import unittest
from unittest.mock import patch

from omniclip_rag import parser as parser_module
from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.service import OmniClipService


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = ROOT / "logseq笔记样本"
TEST_DATA_ROOT = ROOT / ".tmp" / "test_service_data"


class _StubVectorIndex:
    def __init__(self) -> None:
        self.reset_called = False

    def rebuild(self, documents, *, on_progress=None, pause_event=None, cancel_event=None):
        return None

    def upsert(self, documents):
        return None

    def delete(self, chunk_ids):
        return None

    def search(self, query_text, limit):
        return []

    def warmup(self):
        return {"backend": "stub", "model": None, "dimension": 0}

    def reset(self):
        self.reset_called = True


class ServiceTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_DATA_ROOT.exists():
            shutil.rmtree(TEST_DATA_ROOT)
        for path in (
            ROOT / ".tmp" / "watch_vault_test",
            ROOT / ".tmp" / "watch_data_test",
            ROOT / ".tmp" / "duplicate_vault_test",
            ROOT / ".tmp" / "duplicate_data_test",
            ROOT / ".tmp" / "resume_vault_test",
            ROOT / ".tmp" / "resume_data_test",
            ROOT / ".tmp" / "paused_vault_test",
            ROOT / ".tmp" / "paused_data_test",
        ):
            if path.exists():
                shutil.rmtree(path)

    def test_rebuild_and_query(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        try:
            stats = service.rebuild_index()
            self.assertGreaterEqual(stats["files"], 3)
            hits, context_pack = service.query("块嵌入", limit=5)
            self.assertTrue(hits)
            self.assertIn("OmniClip Context Pack", context_pack)
            self.assertIn("Logseq笔记样本", hits[0].title)
        finally:
            service.close()

    def test_reindex_updates_changed_file(self) -> None:
        vault_copy = ROOT / ".tmp" / "watch_vault_test"
        data_root = ROOT / ".tmp" / "watch_data_test"
        shutil.copytree(SAMPLE_ROOT, vault_copy)
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        try:
            service.rebuild_index()
            target = vault_copy / "pages" / "Logseq笔记样本.md"
            with target.open("a", encoding="utf-8") as handle:
                handle.write("\n- 热更新查询验证\n  id:: 22222222-2222-2222-2222-222222222222\n")
            service.reindex_paths(["pages/Logseq笔记样本.md"], [])
            hits, _ = service.query("热更新查询验证", limit=3)
            self.assertTrue(hits)
            self.assertEqual(hits[0].anchor, "热更新查询验证")
        finally:
            service.close()

    def test_rebuild_skips_unreadable_markdown_files(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        original_parse = parser_module.parse_markdown_file

        def side_effect(vault_root, absolute_path):
            if absolute_path.name == "这是一个标题点击后可以进入另外一篇笔记.md":
                raise PermissionError("denied")
            return original_parse(vault_root, absolute_path)

        try:
            with patch("omniclip_rag.parser.parse_markdown_file", side_effect=side_effect):
                stats = service.rebuild_index()
            self.assertGreaterEqual(stats["files"], 1)
            self.assertGreaterEqual(stats["chunks"], 1)
        finally:
            service.close()

    def test_rebuild_demotes_duplicate_block_ids_instead_of_crashing(self) -> None:
        vault_copy = ROOT / '.tmp' / 'duplicate_vault_test'
        data_root = ROOT / '.tmp' / 'duplicate_data_test'
        if vault_copy.exists():
            shutil.rmtree(vault_copy)
        if data_root.exists():
            shutil.rmtree(data_root)
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / 'page_a.md').write_text(
            '- 第一块\n  id:: 11111111-1111-1111-1111-111111111111\n',
            encoding='utf-8',
        )
        (vault_copy / 'page_b.md').write_text(
            '- 第二块\n  id:: 11111111-1111-1111-1111-111111111111\n',
            encoding='utf-8',
        )
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        try:
            stats = service.rebuild_index()
            self.assertEqual(stats['files'], 2)
            self.assertEqual(stats['duplicate_block_ids'], 1)
            rows = service.store.connection.execute(
                'SELECT source_path, block_id, properties_json FROM chunks ORDER BY source_path'
            ).fetchall()
            self.assertEqual(rows[0]['block_id'], '11111111-1111-1111-1111-111111111111')
            self.assertIsNone(rows[1]['block_id'])
            self.assertIn('_duplicate_block_id', rows[1]['properties_json'])
        finally:
            service.close()


    def test_rebuild_waits_while_paused_then_continues(self) -> None:
        vault_copy = ROOT / '.tmp' / 'paused_vault_test'
        data_root = ROOT / '.tmp' / 'paused_data_test'
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / 'page_a.md').write_text('- 第一块\n  id:: cccccccc-cccc-cccc-cccc-cccccccccccc\n', encoding='utf-8')
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        original_parse = parser_module.parse_markdown_file
        parse_called = threading.Event()
        pause_event = threading.Event()
        pause_event.set()
        outcome: dict[str, object] = {}

        def wrapped_parse(vault_root, absolute_path):
            parse_called.set()
            return original_parse(vault_root, absolute_path)

        def worker():
            service = OmniClipService(config, data_paths)
            service.vector_index = _StubVectorIndex()
            try:
                outcome['stats'] = service.rebuild_index(pause_event=pause_event)
            except Exception as exc:
                outcome['error'] = exc
            finally:
                service.close()

        thread = threading.Thread(target=worker, daemon=True)
        with patch('omniclip_rag.parser.parse_markdown_file', side_effect=wrapped_parse):
            thread.start()
            time.sleep(0.25)
            self.assertTrue(thread.is_alive())
            self.assertFalse(parse_called.is_set())
            pause_event.clear()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertNotIn('error', outcome)
        self.assertEqual(outcome['stats']['files'], 1)

    def test_rebuild_can_resume_after_interruption(self) -> None:
        vault_copy = ROOT / '.tmp' / 'resume_vault_test'
        data_root = ROOT / '.tmp' / 'resume_data_test'
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / 'page_a.md').write_text('- 第一块\n  id:: aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa\n', encoding='utf-8')
        (vault_copy / 'page_b.md').write_text('- 第二块\n  id:: bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb\n', encoding='utf-8')
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        original_parse = parser_module.parse_markdown_file
        call_count = {'value': 0}

        def failing_parse(vault_root, absolute_path):
            call_count['value'] += 1
            if call_count['value'] == 2:
                raise RuntimeError('simulated crash')
            return original_parse(vault_root, absolute_path)

        try:
            with patch('omniclip_rag.parser.parse_markdown_file', side_effect=failing_parse):
                with self.assertRaises(RuntimeError):
                    service.rebuild_index()
            pending = service.pending_rebuild()
            self.assertIsNotNone(pending)
            self.assertEqual(pending['completed'], 1)
        finally:
            service.close()

        resumed = OmniClipService(config, data_paths)
        resumed.vector_index = _StubVectorIndex()
        try:
            stats = resumed.rebuild_index(resume=True)
            self.assertEqual(stats['files'], 2)
            self.assertIsNone(resumed.pending_rebuild())
        finally:
            resumed.close()

    def test_estimate_space_records_preflight(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root), vector_backend="lancedb")
        service = OmniClipService(config, data_paths)
        try:
            report = service.estimate_space()
            self.assertGreater(report.vault_file_count, 0)
            self.assertGreater(report.required_free_bytes, 0)
            latest = service.store.fetch_latest_preflight()
            self.assertIsNotNone(latest)
            self.assertEqual(latest["vault_file_count"], report.vault_file_count)
        finally:
            service.close()

    def test_clear_exports_removes_export_files(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        try:
            export_path = data_paths.exports_dir / 'old_context.md'
            export_path.write_text('stale', encoding='utf-8')
            service.clear_data(clear_exports=True)
            self.assertFalse(export_path.exists())
        finally:
            service.close()

    def test_clear_index_also_resets_vector_storage(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        stub = _StubVectorIndex()
        service.vector_index = stub
        try:
            service.rebuild_index()
            self.assertTrue(data_paths.sqlite_file.exists())
            service.clear_data(clear_index=True)
            self.assertTrue(stub.reset_called)
            self.assertEqual(service.store.stats(), {"files": 0, "chunks": 0, "refs": 0})
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
