from pathlib import Path
import shutil
import unittest

from omniclip_rag.models import ChunkRecord, ParsedFile
from omniclip_rag.storage import MetadataStore


ROOT = Path(__file__).resolve().parents[1]
TMP_ROOT = ROOT / ".tmp" / "test_storage_data"


def _build_parsed_file(relative_path: str, count: int) -> ParsedFile:
    absolute_path = TMP_ROOT / "vault" / relative_path
    chunks = [
        ChunkRecord(
            chunk_id=f"chunk-{index}",
            source_path=relative_path,
            kind="markdown",
            block_id=None,
            parent_chunk_id=None,
            title="大页",
            anchor=f"片段 {index}",
            raw_text=f"- 原始文本 {index}",
            position=index,
            depth=0,
            line_start=index + 1,
            line_end=index + 1,
        )
        for index in range(count)
    ]
    return ParsedFile(
        vault_root=TMP_ROOT / "vault",
        absolute_path=absolute_path,
        relative_path=relative_path,
        title="大页",
        kind="markdown",
        chunks=chunks,
        content_hash="hash",
        mtime=1.0,
        size=1,
    )


class MetadataStoreTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TMP_ROOT.exists():
            shutil.rmtree(TMP_ROOT)

    def test_update_rendered_chunks_batches_large_payloads(self) -> None:
        store = MetadataStore(TMP_ROOT / "state" / "metadata.db")
        store._variable_limit = 32
        parsed = _build_parsed_file("large.md", 120)
        try:
            store.replace_file(parsed)
            payloads = [(chunk.chunk_id, f"- 渲染文本 {index}") for index, chunk in enumerate(parsed.chunks)]
            store.update_rendered_chunks(payloads)
            fts_count = store.connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()["count"]
            sample = store.connection.execute(
                "SELECT rendered_text FROM chunks WHERE chunk_id = ?",
                (parsed.chunks[-1].chunk_id,),
            ).fetchone()["rendered_text"]
            self.assertEqual(fts_count, len(parsed.chunks))
            self.assertEqual(sample, "- 渲染文本 119")
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
