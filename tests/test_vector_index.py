import sys
import types
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.vector_index import LanceDbVectorIndex, create_vector_index, is_local_model_ready


ROOT = Path(__file__).resolve().parents[1]
TEST_DATA_ROOT = ROOT / ".tmp" / "test_vector_data"


class FakeEmbedder:
    def encode(self, texts, *, batch_size=16, show_progress_bar=False, normalize_embeddings=True):
        vectors = []
        for text in texts:
            length = float(len(text))
            checksum = float(sum(ord(char) for char in text) % 997)
            vowels = float(sum(char in "aeiouAEIOU块嵌入热更" for char in text))
            vectors.append([length, checksum, vowels])
        return vectors


class VectorIndexTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_DATA_ROOT.exists():
            shutil.rmtree(TEST_DATA_ROOT)

    def test_factory_returns_null_when_disabled(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "factory"))
        config = AppConfig(vault_path=str(ROOT), data_root=str(data_paths.root))
        index = create_vector_index(config, data_paths)
        self.assertEqual(index.search("anything", 5), [])

    def test_lancedb_backend_rebuild_search_and_delete(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "main"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.root),
            vector_backend="lancedb",
        )
        index = LanceDbVectorIndex(config, data_paths, embedder_factory=FakeEmbedder)
        index.rebuild(
            [
                {
                    "chunk_id": "a",
                    "source_path": "pages/a.md",
                    "title": "A",
                    "anchor": "A",
                    "rendered_text": "块嵌入 示例",
                },
                {
                    "chunk_id": "b",
                    "source_path": "pages/b.md",
                    "title": "B",
                    "anchor": "B",
                    "rendered_text": "完全不同的内容",
                },
            ]
        )
        hits = index.search("块嵌入", 2)
        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk_id, "a")

        index.upsert(
            [
                {
                    "chunk_id": "c",
                    "source_path": "pages/c.md",
                    "title": "C",
                    "anchor": "C",
                    "rendered_text": "热更新验证",
                }
            ]
        )
        hits = index.search("热更新验证", 2)
        self.assertEqual(hits[0].chunk_id, "c")

        index.delete(["c"])
        hits = index.search("热更新验证", 3)
        self.assertTrue(all(hit.chunk_id != "c" for hit in hits))

        index.reset()
        self.assertEqual(index.search("块嵌入", 2), [])

    def test_lancedb_warmup_returns_dimension(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "warmup"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.root),
            vector_backend="lancedb",
            vector_model="BAAI/bge-m3",
        )
        index = LanceDbVectorIndex(config, data_paths, embedder_factory=FakeEmbedder)
        result = index.warmup()
        self.assertEqual(result["backend"], "lancedb")
        self.assertEqual(result["model"], "BAAI/bge-m3")
        self.assertEqual(result["dimension"], 3)
        self.assertFalse(result["model_ready"])

    def test_model_ready_requires_weight_file(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "ready"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.root),
            vector_backend="lancedb",
            vector_model="BAAI/bge-m3",
            vector_runtime="torch",
        )
        model_dir = data_paths.cache_dir / "models" / "BAAI__bge-m3"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "modules.json").write_text("{}", encoding="utf-8")
        (model_dir / "config.json").write_text("{}", encoding="utf-8")
        self.assertFalse(is_local_model_ready(config, data_paths))
        (model_dir / "pytorch_model.bin").write_bytes(b"ok")
        self.assertTrue(is_local_model_ready(config, data_paths))

    def test_default_embedder_downloads_model_into_local_cache_without_symlink(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "bootstrap"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.root),
            vector_backend="lancedb",
            vector_model="BAAI/bge-m3",
            vector_runtime="torch",
        )
        calls: dict[str, object] = {}

        def fake_snapshot_download(**kwargs):
            calls["snapshot"] = kwargs
            local_dir = Path(kwargs["local_dir"])
            local_dir.mkdir(parents=True, exist_ok=True)
            (local_dir / "modules.json").write_text("{}", encoding="utf-8")
            (local_dir / "config.json").write_text("{}", encoding="utf-8")
            (local_dir / "pytorch_model.bin").write_bytes(b"ok")
            return str(local_dir)

        class FakeSentenceTransformer:
            def __init__(self, model_name_or_path, **kwargs):
                calls["sentence_transformer"] = {"model_name_or_path": model_name_or_path, **kwargs}

        import lancedb

        fake_hub = types.ModuleType("huggingface_hub")
        fake_hub.snapshot_download = fake_snapshot_download
        fake_hub.constants = types.SimpleNamespace(
            HF_HOME="",
            hf_cache_home="",
            HF_HUB_CACHE="",
            HUGGINGFACE_HUB_CACHE="",
            HUGGINGFACE_ASSETS_CACHE="",
            HF_XET_CACHE="",
            HF_HUB_DISABLE_XET=False,
        )
        fake_sentence_transformers = types.ModuleType("sentence_transformers")
        fake_sentence_transformers.SentenceTransformer = FakeSentenceTransformer

        with patch.dict(
            sys.modules,
            {
                "huggingface_hub": fake_hub,
                "sentence_transformers": fake_sentence_transformers,
            },
        ):
            index = LanceDbVectorIndex(config, data_paths)
            embedder = index._default_embedder_factory()

        self.assertIsInstance(embedder, FakeSentenceTransformer)
        snapshot = calls["snapshot"]
        runtime = calls["sentence_transformer"]
        self.assertEqual(snapshot["repo_id"], "BAAI/bge-m3")
        self.assertFalse(snapshot["local_files_only"])
        self.assertEqual(runtime["model_name_or_path"], snapshot["local_dir"])
        self.assertTrue(runtime["local_files_only"])
        self.assertEqual(runtime["backend"], "torch")
        self.assertEqual(Path(fake_hub.constants.HF_HOME).parent.name, "models")
        self.assertTrue(fake_hub.constants.HF_HUB_DISABLE_XET)


if __name__ == "__main__":
    unittest.main()
