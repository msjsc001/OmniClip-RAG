from pathlib import Path
import shutil
import unittest

from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.service import OmniClipService


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = ROOT / "logseq笔记样本"
TEST_DATA_ROOT = ROOT / ".tmp" / "test_service_data"


class _StubVectorIndex:
    def __init__(self) -> None:
        self.reset_called = False

    def rebuild(self, documents):
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
        for path in (ROOT / ".tmp" / "watch_vault_test", ROOT / ".tmp" / "watch_data_test"):
            if path.exists():
                shutil.rmtree(path)

    def test_rebuild_and_query(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.root))
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
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.root))
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

    def test_estimate_space_records_preflight(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.root), vector_backend="lancedb")
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
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.root))
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
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.root))
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

