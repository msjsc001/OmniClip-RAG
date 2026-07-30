from __future__ import annotations

import unittest
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.app_logging import _close_fault_handler
from omniclip_rag.models import (
    QueryInsights,
    QueryLimitRecommendation,
    QueryResult,
    RerankOutcome,
    SearchHit,
)
from omniclip_rag.query_subprocess import (
    config_to_payload,
    execute_query_request,
    query_worker_command,
    query_worker_environment,
    query_result_from_payload,
    query_result_to_payload,
    write_json_atomic,
)
from omniclip_rag.service import OmniClipService
from omniclip_rag.ui_next_qt.workers import QueryWorker


class MultiVaultQueryWorkerTests(unittest.TestCase):
    def test_query_worker_subprocess_round_trip_uses_synthetic_vault(self) -> None:
        root = Path(tempfile.mkdtemp(prefix='omniclip-query-worker-test-'))
        try:
            vault = root / 'vault'
            vault.mkdir()
            (vault / 'notes.md').write_text(
                '- 我的重要笔记\n  - 子进程查询可以找到这一条\n',
                encoding='utf-8',
            )
            paths = ensure_data_paths(str(root / 'data'), str(vault))
            config = AppConfig(
                vault_path=str(vault),
                data_root=str(paths.global_root),
                vector_backend='disabled',
                reranker_enabled=False,
                query_score_threshold=0.0,
            )
            service = OmniClipService(config, paths)
            try:
                service.rebuild_index()
            finally:
                service.close()

            request_path = root / 'request.json'
            output_path = root / 'result.json'
            progress_path = root / 'progress.json'
            write_json_atomic(request_path, {
                'config': config_to_payload(config),
                'query_text': '重要笔记',
                'copy_result': False,
                'score_threshold': 0.0,
                'allowed_families': ['markdown'],
                'query_mode': 'lexical-only',
            })
            completed = subprocess.run(
                query_worker_command(
                    request_path=request_path,
                    output_path=output_path,
                    progress_path=progress_path,
                ),
                env=query_worker_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(output_path.read_text(encoding='utf-8'))
            self.assertEqual(payload['status'], 'ok')
            self.assertGreaterEqual(len(payload['result']['hits']), 1)
        finally:
            _close_fault_handler()
            logging.shutdown()
            shutil.rmtree(root, ignore_errors=True)

    def test_query_result_round_trip_preserves_nested_insights(self) -> None:
        original = QueryResult(
            hits=[
                SearchHit(
                    score=88.0,
                    title='Title',
                    anchor='Anchor',
                    source_path='page.md',
                    rendered_text='Body',
                    chunk_id='chunk-1',
                )
            ],
            context_text='context',
            insights=QueryInsights(
                runtime_warnings=('warning',),
                runtime_requirements=({
                    'kind': 'reranker',
                    'reason': 'reranker_execution_failed',
                    'error_class': 'OSError',
                    'error_message': 'model shard cannot be opened',
                },),
                trace_lines=('trace',),
                recommendation=QueryLimitRecommendation(
                    device='cpu',
                    preferred=15,
                    minimum=5,
                    maximum=30,
                    reason_code='stable',
                ),
                reranker=RerankOutcome(enabled=True, applied=True),
            ),
        )
        restored = query_result_from_payload(query_result_to_payload(original))
        self.assertEqual(restored.context_text, 'context')
        self.assertEqual(restored.hits[0].chunk_id, 'chunk-1')
        self.assertEqual(restored.insights.runtime_warnings, ('warning',))
        self.assertEqual(restored.insights.runtime_requirements[0]['error_class'], 'OSError')
        self.assertEqual(restored.insights.recommendation.preferred, 15)
        self.assertTrue(restored.insights.reranker.applied)

    def test_isolated_worker_recovers_native_crash_with_lexical_process(self) -> None:
        config = AppConfig(
            vault_path=str(Path('vault').resolve()),
            data_root=str(Path('data-root').resolve()),
        )
        worker = QueryWorker(
            config=config,
            paths=SimpleNamespace(),
            query_text='test',
            copy_result=False,
            score_threshold=0.0,
            allowed_families=('markdown',),
        )
        result = QueryResult(hits=[], context_text='', insights=QueryInsights())
        success_payload = {
            'status': 'ok',
            'query_text': 'test',
            'copied': False,
            'score_threshold': 0.0,
            'allowed_families': ['markdown'],
            'result': query_result_to_payload(result),
            'status_snapshot': {},
        }
        successes = []
        failures = []
        worker.succeeded.connect(successes.append)
        worker.failed.connect(lambda message, detail: failures.append((message, detail)))
        with patch.object(
            worker,
            '_run_child',
            side_effect=[
                (None, '查询子进程异常退出（代码 3221225477）'),
                (success_payload, ''),
            ],
        ) as run_child:
            worker._run()

        self.assertEqual(len(successes), 1)
        self.assertEqual(failures, [])
        self.assertIn(
            'semantic_query_process_recovered',
            successes[0].result.insights.runtime_warnings,
        )
        self.assertEqual(run_child.call_args_list[1].args[0]['query_mode'], 'lexical-only')

    def test_multi_vault_query_reuses_reranker_and_releases_models_once(self) -> None:
        vaults = [str(Path('vault-a').resolve()), str(Path('vault-b').resolve())]
        config = AppConfig(
            vault_path=vaults[0],
            data_root=str(Path('data-root').resolve()),
            md_selected_vault_paths=vaults,
            query_limit=15,
        )
        instances = []

        class FakeService:
            def __init__(self, service_config, paths) -> None:
                self.config = service_config
                self.paths = paths
                self.reranker = object()
                self.close_calls: list[bool] = []
                instances.append(self)

            def status_snapshot(self):
                return {'query_available_families': ['markdown']}

            def query(self, *_args, **_kwargs):
                return QueryResult(
                    hits=[
                        SearchHit(
                            score=75.0,
                            title=Path(self.config.vault_path).name,
                            anchor='A',
                            source_path='page.md',
                            rendered_text='result',
                            chunk_id=self.config.vault_path,
                        )
                    ],
                    context_text='',
                    insights=QueryInsights(
                        runtime_warnings=('markdown_reranker_unavailable',),
                        runtime_requirements=({
                            'kind': 'reranker',
                            'reason': 'reranker_execution_failed',
                            'error_class': 'OSError',
                            'error_message': 'model shard cannot be opened',
                        },),
                    ),
                )

            def close(self, *, release_process_resources=True) -> None:
                self.close_calls.append(bool(release_process_resources))

        request = {
            'config': config_to_payload(config),
            'query_text': 'test',
            'copy_result': False,
            'score_threshold': 0.0,
            'allowed_families': ['markdown'],
            'query_mode': 'hybrid',
        }
        with patch('omniclip_rag.query_subprocess.ensure_data_paths', return_value=SimpleNamespace()), \
             patch('omniclip_rag.query_subprocess.OmniClipService', FakeService), \
             patch('omniclip_rag.query_subprocess.release_process_query_resources') as release_mock:
            result, snapshot = execute_query_request(request)

        self.assertEqual(len(instances), 2)
        self.assertIs(instances[1].reranker, instances[0].reranker)
        self.assertEqual([service.close_calls for service in instances], [[False], [False]])
        release_mock.assert_called_once_with()
        self.assertEqual(len(result.hits), 2)
        self.assertEqual(result.insights.runtime_warnings, ('markdown_reranker_unavailable',))
        self.assertEqual(len(result.insights.runtime_requirements), 2)
        self.assertEqual(
            {Path(str(item['vault_path'])).name for item in result.insights.runtime_requirements},
            {'vault-a', 'vault-b'},
        )
        self.assertEqual(snapshot['mode'], 'multi_vault_fanout')


if __name__ == '__main__':
    unittest.main()
