import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace

import omniclip_rag  # noqa: F401

from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.extensions.query import ExtensionQueryBroker
from omniclip_rag.models import SearchHit
from omniclip_rag.service import OmniClipService

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / '.tmp' / 'test_extension_query'


class ExtensionQueryBrokerTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def test_rrf_fusion_keeps_first_rank_of_each_family_competitive(self) -> None:
        paths = ensure_data_paths(str(TEST_ROOT / 'data'))
        broker = ExtensionQueryBroker(config=AppConfig(vault_path='', data_root=str(paths.global_root)), paths=paths)
        markdown_hits = [
            SearchHit(score=11.0, title='Markdown A', anchor='A', source_path='a.md', rendered_text='A', chunk_id='m1', source_family='markdown', source_kind='markdown'),
            SearchHit(score=10.0, title='Markdown B', anchor='B', source_path='b.md', rendered_text='B', chunk_id='m2', source_family='markdown', source_kind='markdown'),
            SearchHit(score=9.0, title='Markdown C', anchor='C', source_path='c.md', rendered_text='C', chunk_id='m3', source_family='markdown', source_kind='markdown'),
        ]
        pdf_hits = [
            SearchHit(score=98.0, title='PDF · guide.pdf · 第 1 页', anchor='第 1 页', source_path='guide.pdf', rendered_text='pdf', chunk_id='p1', source_family='pdf', source_kind='pdf'),
        ]
        tika_hits = [
            SearchHit(score=96.0, title='DOCX(Tika) · guide.docx', anchor='Guide', source_path='guide.docx', rendered_text='docx', chunk_id='t1', source_family='tika', source_kind='docx'),
        ]

        fused = broker.fuse_family_hits({'markdown': markdown_hits, 'pdf': pdf_hits, 'tika': tika_hits}, limit=3)

        self.assertGreaterEqual(len(fused), 3)
        self.assertEqual({hit.source_family for hit in fused[:3]}, {'markdown', 'pdf', 'tika'})
        score_by_family = {hit.source_family: hit.score for hit in fused[:3]}
        self.assertEqual(score_by_family['markdown'], 11.0)
        self.assertEqual(score_by_family['pdf'], 98.0)
        self.assertEqual(score_by_family['tika'], 96.0)

    def test_service_query_respects_allowed_families_for_extension_only_search(self) -> None:
        vault = TEST_ROOT / 'vault'
        vault.mkdir(parents=True, exist_ok=True)
        (vault / 'note.md').write_text('# Main\n\nMarkdown token only.\n', encoding='utf-8')
        paths = ensure_data_paths(str(TEST_ROOT / 'data_service'), str(vault))
        config = AppConfig(vault_path=str(vault), data_root=str(paths.global_root), vector_backend='disabled', reranker_enabled=False)
        service = OmniClipService(config, paths)
        real_broker = ExtensionQueryBroker(config=config, paths=paths)
        seen_allowed: list[tuple[str, ...]] = []

        def collect_extension_hits(query_text: str, *, limit: int, profile, allowed_families: set[str] | None = None):
            del query_text, limit, profile
            normalized = tuple(sorted(allowed_families or set()))
            seen_allowed.append(normalized)
            result: dict[str, list[SearchHit]] = {}
            if 'pdf' in (allowed_families or set()):
                result['pdf'] = [
                    SearchHit(
                        score=88.0,
                        title='PDF · guide.pdf · 第 1 页',
                        anchor='第 1 页',
                        source_path='guide.pdf',
                        rendered_text='extension token',
                        chunk_id='pdf-1',
                        source_family='pdf',
                        source_kind='pdf',
                        source_label='PDF · guide.pdf · 第 1 页',
                        page_no=1,
                    )
                ]
            if 'tika' in (allowed_families or set()):
                result['tika'] = [
                    SearchHit(
                        score=86.0,
                        title='DOCX(Tika) · guide.docx',
                        anchor='Guide',
                        source_path='guide.docx',
                        rendered_text='extension token',
                        chunk_id='tika-1',
                        source_family='tika',
                        source_kind='docx',
                        source_label='DOCX(Tika) · guide.docx',
                    )
                ]
            return result

        service.extension_query_broker = SimpleNamespace(
            collect_extension_hits=collect_extension_hits,
            fuse_family_hits=real_broker.fuse_family_hits,
            close=lambda: None,
        )
        try:
            result = service.query('extension token', limit=5, score_threshold=0, allowed_families={'pdf'})
        finally:
            service.close()

        self.assertEqual(seen_allowed[-1], ('pdf',))
        self.assertTrue(result.hits)
        self.assertTrue(all(hit.source_family == 'pdf' for hit in result.hits))
