from pathlib import Path
import unittest

from omniclip_rag.parser import parse_markdown_file


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = ROOT / "笔记样本"


class ParserTests(unittest.TestCase):
    def test_logseq_sample_extracts_blocks_and_refs(self) -> None:
        parsed = parse_markdown_file(SAMPLE_ROOT, SAMPLE_ROOT / "pages" / "笔记样本.md")
        self.assertEqual(parsed.kind, "logseq")
        self.assertTrue(parsed.page_properties)
        block_ids = {chunk.block_id for chunk in parsed.chunks if chunk.block_id}
        self.assertIn("68a922fb-da84-41fa-aa7e-e741d66a0a6f", block_ids)
        self.assertTrue(any(ref_type == "embed" for chunk in parsed.chunks for ref_type, _ in chunk.refs))

    def test_bullet_only_outline_without_id_is_still_chunked_as_outline(self) -> None:
        vault = ROOT / ".tmp" / "parser_outline_vault"
        if vault.exists():
            import shutil
            shutil.rmtree(vault)
        vault.mkdir(parents=True, exist_ok=True)
        target = vault / "bullet_outline.md"
        target.write_text(
            "- 鞋子记录\n  - 棕色鞋\n    - 9000步\n- 鞋底胶综合硬度不如木片\n",
            encoding="utf-8",
        )
        try:
            parsed = parse_markdown_file(vault, target)
            self.assertEqual(parsed.kind, "logseq")
            self.assertGreaterEqual(len(parsed.chunks), 4)
        finally:
            import shutil
            shutil.rmtree(vault)

    def test_empty_page_becomes_stub(self) -> None:
        parsed = parse_markdown_file(SAMPLE_ROOT, SAMPLE_ROOT / "pages" / "这是一个标题点击后可以进入另外一篇笔记.md")
        self.assertEqual(len(parsed.chunks), 1)
        self.assertEqual(parsed.chunks[0].kind, "page_stub")


if __name__ == "__main__":
    unittest.main()
