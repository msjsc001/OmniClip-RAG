import shutil
import unittest
from pathlib import Path

from omniclip_rag.models import SearchHit
from omniclip_rag.query_runtime import QueryRuntimeAdvisor, select_query_hits


ROOT = Path(__file__).resolve().parents[1]
TEST_STATE_ROOT = ROOT / ".tmp" / "test_query_runtime"


class QueryRuntimeTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_STATE_ROOT.exists():
            shutil.rmtree(TEST_STATE_ROOT)

    def test_select_query_hits_dedupes_same_page_overlap_but_keeps_complementary_fragment(self) -> None:
        hits = [
            SearchHit(
                score=98.0,
                title="手机笔记",
                anchor="鞋子记录",
                source_path="pages/手机笔记.md",
                rendered_text="- 鞋子记录\n  - 棕色鞋\n    - 20260219 9000步\n  - 白鞋\n    - 20250228 7000步最跟部已经磨开",
                display_text="- 鞋子记录\n  - 棕色鞋\n    - 20260219 9000步\n  - 白鞋\n    - 20250228 7000步最跟部已经磨开",
                chunk_id="root",
            ),
            SearchHit(
                score=94.0,
                title="手机笔记",
                anchor="鞋子记录 > 棕色鞋",
                source_path="pages/手机笔记.md",
                rendered_text="- 鞋子记录\n  - 棕色鞋\n    - 20260219 9000步",
                display_text="- 鞋子记录\n  - 棕色鞋\n    - 20260219 9000步",
                chunk_id="child",
            ),
            SearchHit(
                score=92.0,
                title="手机笔记",
                anchor="鞋底胶综合硬度不如木片，是否能垫小木片",
                source_path="pages/手机笔记.md",
                rendered_text="- 鞋底胶综合硬度不如木片，是否能垫小木片",
                display_text="- 鞋底胶综合硬度不如木片，是否能垫小木片",
                chunk_id="other",
            ),
        ]

        selected, insights = select_query_hits(hits, 3)

        self.assertEqual([hit.chunk_id for hit in selected], ["root", "other"])
        self.assertEqual(insights.selected_hits, 2)
        self.assertGreaterEqual(insights.suppressed_duplicates, 1)
        self.assertEqual(insights.page_diversity, 1)

    def test_query_runtime_advisor_returns_cpu_baseline_without_history(self) -> None:
        advisor = QueryRuntimeAdvisor(TEST_STATE_ROOT / 'query_runtime.json')

        recommendation = advisor.current_recommendation('cpu')

        self.assertEqual(recommendation.device, 'cpu')
        self.assertEqual(recommendation.preferred, 15)
        self.assertEqual(recommendation.minimum, 8)
        self.assertEqual(recommendation.maximum, 24)
        self.assertEqual(recommendation.reason_code, 'baseline')

    def test_query_runtime_advisor_uses_reranker_baseline_for_cuda(self) -> None:
        advisor = QueryRuntimeAdvisor(TEST_STATE_ROOT / 'query_runtime.json')

        recommendation = advisor.current_recommendation('cuda', reranker_enabled=True)

        self.assertEqual(recommendation.device, 'cuda')
        self.assertEqual(recommendation.preferred, 50)
        self.assertEqual(recommendation.minimum, 30)
        self.assertEqual(recommendation.maximum, 70)

    def test_query_runtime_advisor_shrinks_range_after_slow_queries(self) -> None:
        advisor = QueryRuntimeAdvisor(TEST_STATE_ROOT / 'query_runtime.json')
        for _ in range(4):
            recommendation = advisor.record_and_recommend(
                resolved_device='cpu',
                query_limit=24,
                elapsed_ms=2600,
                selected_hits=18,
                hydrated_candidates=28,
            )

        self.assertEqual(recommendation.device, 'cpu')
        self.assertEqual(recommendation.reason_code, 'slow')
        self.assertLess(recommendation.preferred, 24)
        self.assertLessEqual(recommendation.maximum, 24)
        self.assertGreaterEqual(recommendation.minimum, 8)


if __name__ == '__main__':
    unittest.main()
