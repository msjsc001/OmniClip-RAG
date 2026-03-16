import unittest

from omniclip_rag.models import QueryInsights
from omniclip_rag.retrieval_policy import build_query_profile
from omniclip_rag.service import OmniClipService


class QueryTracePlanningTests(unittest.TestCase):
    def test_query_insights_exposes_structured_trace_fields(self) -> None:
        insights = QueryInsights()
        self.assertEqual(insights.query_plan, {})
        self.assertEqual(insights.query_fingerprint, {})
        self.assertEqual(insights.query_stage, {})

    def test_normalize_query_mode_aliases(self) -> None:
        self.assertEqual(OmniClipService._normalize_query_mode(None), 'hybrid')
        self.assertEqual(OmniClipService._normalize_query_mode('vector_only'), 'vector-only')
        self.assertEqual(OmniClipService._normalize_query_mode('lexical'), 'lexical-only')
        self.assertEqual(OmniClipService._normalize_query_mode('hybrid-no-rerank'), 'hybrid_no_rerank')

    def test_normalize_device_policy_aliases(self) -> None:
        self.assertEqual(OmniClipService._normalize_device_policy(None, 'auto'), 'prefer-cuda')
        self.assertEqual(OmniClipService._normalize_device_policy('require_cuda', 'cpu'), 'require-cuda')
        self.assertEqual(OmniClipService._normalize_device_policy('cpu', 'cuda'), 'prefer-cpu')
        self.assertEqual(OmniClipService._normalize_device_policy('gpu', 'cpu'), 'prefer-cuda')

    def test_expected_steps_reflect_four_modes(self) -> None:
        profile = build_query_profile('我的思维', 30)
        lexical_steps = OmniClipService._query_expected_steps(
            markdown_requested=True,
            query_mode='lexical-only',
            profile=profile,
            requested_families={'markdown'},
            reranker_enabled=False,
        )
        self.assertIn('Markdown 基础候选召回', lexical_steps)
        self.assertNotIn('Markdown 语义向量召回', lexical_steps)

        vector_steps = OmniClipService._query_expected_steps(
            markdown_requested=True,
            query_mode='vector-only',
            profile=profile,
            requested_families={'markdown'},
            reranker_enabled=False,
        )
        self.assertNotIn('Markdown 基础候选召回', vector_steps)
        self.assertIn('Markdown 语义向量召回', vector_steps)

        hybrid_steps = OmniClipService._query_expected_steps(
            markdown_requested=True,
            query_mode='hybrid',
            profile=profile,
            requested_families={'markdown'},
            reranker_enabled=True,
        )
        self.assertIn('Markdown 基础候选召回', hybrid_steps)
        self.assertIn('Markdown 语义向量召回', hybrid_steps)
        self.assertIn('Reranker', hybrid_steps)

        hybrid_no_rerank_steps = OmniClipService._query_expected_steps(
            markdown_requested=True,
            query_mode='hybrid_no_rerank',
            profile=profile,
            requested_families={'markdown'},
            reranker_enabled=False,
        )
        self.assertIn('Markdown 基础候选召回', hybrid_no_rerank_steps)
        self.assertIn('Markdown 语义向量召回', hybrid_no_rerank_steps)
        self.assertNotIn('Reranker', hybrid_no_rerank_steps)


if __name__ == '__main__':
    unittest.main()
