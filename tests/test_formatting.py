import unittest

from omniclip_rag.formatting import format_space_report, summarize_preflight
from omniclip_rag.models import SpaceEstimate


class FormattingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = SpaceEstimate(
            run_at='2026-03-07T00:00:00Z',
            vault_file_count=10,
            vault_total_bytes=1024,
            parsed_chunk_count=20,
            ref_count=5,
            logseq_file_count=6,
            markdown_file_count=4,
            estimated_sqlite_bytes=2048,
            estimated_fts_bytes=4096,
            estimated_vector_bytes=8192,
            estimated_model_bytes=16384,
            estimated_peak_temp_bytes=32768,
            safety_margin_bytes=65536,
            current_state_bytes=128,
            current_model_cache_bytes=256,
            required_free_bytes=131072,
            available_free_bytes=262144,
            vector_backend='lancedb',
            vector_model='BAAI/bge-m3',
            can_proceed=True,
            risk_level='medium',
            estimated_build_seconds=95,
            estimated_download_seconds=180,
            notes=['sample note'],
        )

    def test_format_space_report_localizes_to_chinese(self) -> None:
        report_text = format_space_report(self.report, 'zh-CN')
        self.assertIn('风险等级', report_text)
        self.assertIn('首轮建库时间', report_text)

    def test_format_space_report_localizes_to_english(self) -> None:
        report_text = format_space_report(self.report, 'en')
        self.assertIn('Risk level', report_text)
        self.assertIn('First full-build time', report_text)

    def test_summarize_preflight_localizes(self) -> None:
        self.assertIn('建库', summarize_preflight(self.report, 'zh-CN'))
        self.assertIn('Build', summarize_preflight(self.report, 'en'))


if __name__ == '__main__':
    unittest.main()
