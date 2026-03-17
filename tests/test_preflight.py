import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from omniclip_rag import parser as parser_module
from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.preflight import estimate_storage_for_vault


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = ROOT / "笔记样本"
TEST_DATA_ROOT = ROOT / ".tmp" / "test_preflight_data"


class PreflightTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_DATA_ROOT.exists():
            shutil.rmtree(TEST_DATA_ROOT)

    def test_estimate_space_skips_unreadable_markdown_files(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root), ui_language='zh-CN')
        original_parse = parser_module.parse_markdown_file
        sample_path = SAMPLE_ROOT / "pages" / "笔记样本.md"

        call_count = {"value": 0}

        def side_effect(vault_root, absolute_path):
            call_count["value"] += 1
            if call_count["value"] == 1:
                raise PermissionError("denied")
            return original_parse(vault_root, absolute_path)

        with patch("omniclip_rag.preflight.parse_markdown_file", side_effect=side_effect):
            report = estimate_storage_for_vault(config, data_paths, files=[sample_path, sample_path])

        self.assertEqual(report.vault_file_count, 1)
        self.assertGreater(report.estimated_build_seconds, 0)
        self.assertTrue(any("不可读" in note for note in report.notes))


if __name__ == '__main__':
    unittest.main()
