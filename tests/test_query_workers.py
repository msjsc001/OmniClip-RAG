from __future__ import annotations

import unittest
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.app_logging import _close_fault_handler
from omniclip_rag.errors import BuildCancelledError
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
    run_query_worker,
    write_json_atomic,
)
from omniclip_rag.service import OmniClipService
from omniclip_rag.ui_next_qt.workers import QueryWorker, WatchWorker


class MultiVaultQueryWorkerTests(unittest.TestCase):
    def test_watch_worker_stop_waits_for_thread_and_keeps_monitor_lightweight(self) -> None:
        entered = threading.Event()
        close_release_values: list[bool] = []

        class FakeService:
            def __init__(self, config, paths) -> None:
                pass

            def watch_until_stopped(self, stop_event, **kwargs) -> None:
                self.reindex_runner = kwargs.get('reindex_runner')
                entered.set()
                stop_event.wait(2.0)

            def close(self, *, release_process_resources: bool = True) -> None:
                close_release_values.append(release_process_resources)

        worker = WatchWorker(
            config=SimpleNamespace(vault_path='C:/vault'),
            paths=SimpleNamespace(),
            interval=0.01,
            force_polling=True,
        )
        with patch('omniclip_rag.ui_next_qt.workers.OmniClipService', FakeService):
            worker.start()
            self.assertTrue(entered.wait(1.0))
            self.assertTrue(worker.is_running())
            worker.stop()
            self.assertTrue(worker.wait(timeout=2.0))
        self.assertFalse(worker.is_running())
        self.assertEqual(close_release_values, [True])

    def test_watch_worker_stop_terminates_and_reaps_active_reindex_child(self) -> None:
        root = Path(tempfile.mkdtemp(prefix='omniclip-watch-stop-test-'))
        try:
            worker = WatchWorker(
                config=AppConfig(vault_path=str(root), data_root=str(root / 'data')),
                paths=SimpleNamespace(),
                interval=10.0,
                force_polling=False,
            )
            errors: list[BaseException] = []

            def run_child() -> None:
                try:
                    worker._run_reindex_child(['page.md'], [], threading.Event())
                except BaseException as exc:
                    errors.append(exc)

            with patch(
                'omniclip_rag.ui_next_qt.workers.watch_reindex_worker_command',
                return_value=[sys.executable, '-c', 'import time; time.sleep(30)'],
            ):
                thread = threading.Thread(target=run_child)
                thread.start()
                deadline = time.monotonic() + 5.0
                while worker._process is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                process = worker._process
                self.assertIsNotNone(process)
                worker.stop()
                thread.join(timeout=5.0)
            self.assertFalse(thread.is_alive())
            assert process is not None
            self.assertIsNotNone(process.poll())
            self.assertTrue(errors)
            self.assertIsInstance(errors[0], BuildCancelledError)
        finally:
            shutil.rmtree(root, ignore_errors=True)

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
                reranker=RerankOutcome(
                    enabled=True,
                    applied=True,
                    resource_snapshot={'commit_headroom_bytes': 6 * 1024**3},
                    resource_requirements={'min_commit_headroom_bytes': 4 * 1024**3},
                ),
            ),
        )
        restored = query_result_from_payload(query_result_to_payload(original))
        self.assertEqual(restored.context_text, 'context')
        self.assertEqual(restored.hits[0].chunk_id, 'chunk-1')
        self.assertEqual(restored.insights.runtime_warnings, ('warning',))
        self.assertEqual(restored.insights.runtime_requirements[0]['error_class'], 'OSError')
        self.assertEqual(restored.insights.recommendation.preferred, 15)
        self.assertTrue(restored.insights.reranker.applied)
        self.assertEqual(
            restored.insights.reranker.resource_snapshot['commit_headroom_bytes'],
            6 * 1024**3,
        )
        self.assertEqual(
            restored.insights.reranker.resource_requirements['min_commit_headroom_bytes'],
            4 * 1024**3,
        )

    def test_isolated_worker_reaps_completed_child_process(self) -> None:
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

        class FakeProcess:
            def __init__(self) -> None:
                self.returncode = 0
                self.wait_calls = 0

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                del timeout
                self.wait_calls += 1
                return self.returncode

            def kill(self):
                self.returncode = -9

        process = FakeProcess()
        with patch(
            'omniclip_rag.ui_next_qt.workers.subprocess.Popen',
            return_value=process,
        ):
            payload, message = worker._run_child(worker._request_payload())

        self.assertIsNone(payload)
        self.assertIn('异常退出', message)
        self.assertGreaterEqual(process.wait_calls, 2)
        self.assertIsNone(worker._process)

    def test_isolated_worker_reaps_cancelled_child_process(self) -> None:
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

        class FakeProcess:
            def __init__(self) -> None:
                self.returncode = None
                self.killed = False
                self.wait_calls = 0

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                del timeout
                self.wait_calls += 1
                if self.returncode is None:
                    self.returncode = -9
                return self.returncode

            def kill(self):
                self.killed = True
                self.returncode = -9

        process = FakeProcess()
        worker._cancel_event.set()
        with patch(
            'omniclip_rag.ui_next_qt.workers.subprocess.Popen',
            return_value=process,
        ):
            payload, message = worker._run_child(worker._request_payload())

        self.assertIsNone(payload)
        self.assertEqual(message, '查询已取消')
        self.assertTrue(process.killed)
        self.assertGreaterEqual(process.wait_calls, 1)
        self.assertIsNone(worker._process)

    def test_isolated_worker_recovers_native_crash_with_cpu_semantic_process(self) -> None:
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
            'semantic_query_process_cpu_recovered',
            successes[0].result.insights.runtime_warnings,
        )
        recovery_request = run_child.call_args_list[1].args[0]
        self.assertEqual(recovery_request['query_mode'], 'hybrid_no_rerank')
        self.assertEqual(recovery_request['config']['vector_device'], 'cpu')

    def test_isolated_worker_uses_lexical_process_after_cpu_semantic_crash(self) -> None:
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
        worker.succeeded.connect(successes.append)
        with patch.object(
            worker,
            '_run_child',
            side_effect=[
                (None, 'GPU semantic crash'),
                (None, 'CPU semantic crash'),
                (success_payload, ''),
            ],
        ) as run_child:
            worker._run()

        self.assertEqual(len(successes), 1)
        self.assertIn(
            'semantic_query_process_recovered',
            successes[0].result.insights.runtime_warnings,
        )
        self.assertEqual(run_child.call_args_list[2].args[0]['query_mode'], 'lexical-only')

    def test_multi_vault_query_worker_keeps_models_until_process_exit(self) -> None:
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
            result, snapshot = execute_query_request(
                request,
                release_process_resources=False,
            )

        self.assertEqual(len(instances), 2)
        self.assertIs(instances[1].reranker, instances[0].reranker)
        self.assertEqual([service.close_calls for service in instances], [[False], [False]])
        release_mock.assert_not_called()
        self.assertEqual(len(result.hits), 2)
        self.assertEqual(result.insights.runtime_warnings, ('markdown_reranker_unavailable',))
        self.assertEqual(len(result.insights.runtime_requirements), 2)
        self.assertEqual(
            {Path(str(item['vault_path'])).name for item in result.insights.runtime_requirements},
            {'vault-a', 'vault-b'},
        )
        self.assertEqual(snapshot['mode'], 'multi_vault_fanout')

    def test_query_worker_disables_explicit_process_resource_cleanup(self) -> None:
        root = Path(tempfile.mkdtemp(prefix='omniclip-query-worker-cleanup-test-'))
        try:
            request_path = root / 'request.json'
            output_path = root / 'result.json'
            progress_path = root / 'progress.json'
            write_json_atomic(request_path, {
                'query_text': 'test',
                'copy_result': False,
                'score_threshold': 0.0,
                'allowed_families': ['markdown'],
            })
            result = QueryResult(hits=[], context_text='', insights=QueryInsights())
            with patch(
                'omniclip_rag.query_subprocess.execute_query_request',
                return_value=(result, {}),
            ) as execute_mock:
                return_code = run_query_worker(
                    request_path=str(request_path),
                    output_path=str(output_path),
                    progress_path=str(progress_path),
                )

            self.assertEqual(return_code, 0)
            self.assertFalse(execute_mock.call_args.kwargs['release_process_resources'])
            self.assertEqual(
                json.loads(output_path.read_text(encoding='utf-8'))['status'],
                'ok',
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
