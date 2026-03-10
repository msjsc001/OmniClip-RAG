import unittest

from omniclip_rag.retrieval_policy import build_query_profile, rank_candidates


class RetrievalPolicyTests(unittest.TestCase):
    def test_single_character_profile_disables_vector_and_expands_candidates(self) -> None:
        profile = build_query_profile('鞋', 15)
        self.assertEqual(profile.kind, 'single_char')
        self.assertFalse(profile.use_vector)
        self.assertGreaterEqual(profile.candidate_limit, 240)

    def test_concept_profile_keeps_vector_enabled(self) -> None:
        profile = build_query_profile('思维模型', 15)
        self.assertEqual(profile.kind, 'concept')
        self.assertTrue(profile.use_vector)
        self.assertGreaterEqual(profile.candidate_limit, 96)

    def test_natural_query_profile_prefers_balanced_retrieval(self) -> None:
        profile = build_query_profile('压力反应和识人模型有什么关系？', 15)
        self.assertEqual(profile.kind, 'natural_query')
        self.assertTrue(profile.use_vector)
        self.assertGreater(profile.rrf_weight, 0.8 - 0.01)

    def test_rank_candidates_uses_vector_support_without_overriding_direct_hit(self) -> None:
        rows = [
            {
                'chunk_id': 'direct',
                'title': '思维模型',
                'anchor': '思维模型',
                'source_path': 'pages/a.md',
                'rendered_text': '- 思维模型\n  - 定义',
                'fts_rank': 0.0,
                'like_hits': 3,
            },
            {
                'chunk_id': 'semantic',
                'title': '高度整合的思辨者',
                'anchor': '卓越的元认知与思维框架',
                'source_path': 'pages/b.md',
                'rendered_text': '- 学识（思维模型）和经验的结合',
                'fts_rank': None,
                'like_hits': 0,
            },
        ]
        profile = build_query_profile('思维模型', 15)
        hits = rank_candidates('思维模型', rows, {'semantic': 0.92}, profile)
        self.assertEqual(hits[0].chunk_id, 'direct')
        self.assertEqual(hits[1].chunk_id, 'semantic')
        self.assertIn('语义相似', hits[1].reason)


if __name__ == '__main__':
    unittest.main()
