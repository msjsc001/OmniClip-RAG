import json
import io
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from omniclip_rag.app_entry import desktop


class DesktopEntryTests(unittest.TestCase):
    def test_main_dispatches_selfcheck_query_without_launching_qt(self) -> None:
        with patch.object(desktop, '_apply_runtime_layout_if_needed') as runtime_mock, \
             patch.object(desktop, 'run_selfcheck_query', return_value=7) as mocked:
            result = desktop.main([
                '--selfcheck-query',
                '--vault', 'D:/vault',
                '--data-root', 'D:/data',
                '--query', '我的思维',
                '--limit', '30',
                '--threshold', '0',
                '--output', 'out.json',
            ])
        self.assertEqual(result, 7)
        runtime_mock.assert_called_once()
        mocked.assert_called_once_with(
            vault_path='D:/vault',
            data_root='D:/data',
            query_text='我的思维',
            limit=30,
            threshold=0.0,
            output_path='out.json',
            query_mode='hybrid',
        )

    def test_main_ignores_empty_selfcheck_flag_and_launches_qt(self) -> None:
        with patch.object(desktop, '_apply_runtime_layout_if_needed') as runtime_mock, \
             patch.object(desktop, 'launch_desktop', return_value=3) as mocked:
            result = desktop.main(['--selfcheck-query'])
        self.assertEqual(result, 3)
        runtime_mock.assert_called_once()
        mocked.assert_called_once_with('next')

    def test_main_dispatches_runtime_selfcheck_without_launching_qt(self) -> None:
        with patch.object(desktop, '_apply_runtime_layout_if_needed') as runtime_mock, \
             patch.object(desktop, 'run_selfcheck_runtime', return_value=5) as mocked:
            result = desktop.main([
                '--selfcheck-runtime', 'suite',
                '--output', 'runtime.json',
            ])
        self.assertEqual(result, 5)
        runtime_mock.assert_called_once()
        mocked.assert_called_once_with(
            check_kind='suite',
            output_path='runtime.json',
        )

    def test_main_dispatches_download_worker_without_launching_qt(self) -> None:
        with patch.object(desktop, '_apply_runtime_layout_if_needed') as runtime_mock, \
             patch.object(desktop, 'run_download_worker', return_value=11) as mocked:
            result = desktop.main([
                '--download-worker',
                '--download-kind', 'vector',
                '--repo-id', 'BAAI/bge-m3',
                '--target-dir', 'D:/data/models/BAAI__bge-m3',
                '--hf-home', 'D:/data/models/_hf_home',
                '--source', 'mirror',
                '--log-path', 'D:/logs/download.log',
                '--pid-path', 'D:/logs/download.pid',
                '--result-path', 'D:/logs/download.result.json',
            ])
        self.assertEqual(result, 11)
        runtime_mock.assert_called_once()
        mocked.assert_called_once_with(
            download_kind='vector',
            repo_id='BAAI/bge-m3',
            target_dir='D:/data/models/BAAI__bge-m3',
            hf_home='D:/data/models/_hf_home',
            download_source='mirror',
            log_path='D:/logs/download.log',
            pid_path='D:/logs/download.pid',
            result_path='D:/logs/download.result.json',
            local_files_only=False,
        )

    def test_run_selfcheck_runtime_suite_writes_combined_payload(self) -> None:
        temp_dir = Path(__file__).resolve().parents[1] / '.tmp' / 'desktop_runtime_suite'
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            output = temp_dir / 'runtime_suite.json'
            with patch('omniclip_rag.vector_index.inspect_runtime_environment', return_value={'runtime_complete': True}), \
                 patch('omniclip_rag.vector_index.detect_acceleration', return_value={'cuda_available': True}), \
                 patch('omniclip_rag.vector_index.probe_runtime_gpu_execution', return_value={'success': True, 'state': 'verified'}), \
                 patch('omniclip_rag.app_entry.desktop.run_gpu_query_canary', return_value={'success': True, 'query_stage': {'vector_actual_device': 'cuda:0'}}):
                result = desktop.run_selfcheck_runtime(check_kind='suite', output_path=str(output))
            self.assertEqual(result, 0)
            payload = json.loads(output.read_text(encoding='utf-8'))
            self.assertTrue(payload['ok'])
            self.assertEqual(payload['check_kind'], 'suite')
            self.assertTrue(payload['gpu_smoke']['success'])
            self.assertTrue(payload['gpu_query_canary']['success'])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_write_selfcheck_payload_serializes_paths(self) -> None:
        target_dir = Path(__file__).resolve().parents[1] / '.tmp' / 'desktop_entry'
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target = target_dir / 'selfcheck.json'
        desktop._write_selfcheck_payload(target, {'path': Path('D:/vault')})
        payload = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(payload['path'], str(Path('D:/vault')))
        shutil.rmtree(target_dir)

    def test_download_heartbeat_emits_waiting_then_growth_snapshots(self) -> None:
        target_dir = Path(tempfile.mkdtemp(prefix='omniclip-download-target-'))
        cache_dir = Path(tempfile.mkdtemp(prefix='omniclip-download-cache-'))
        messages: list[str] = []
        try:
            stop_event, thread = desktop._start_download_heartbeat(
                emit=messages.append,
                target_dir=target_dir,
                repo_cache_dir=cache_dir,
                interval_seconds=0.05,
            )
            time.sleep(0.08)
            (target_dir / 'model.bin').write_bytes(b'x' * 4096)
            cache_nested = cache_dir / 'nested'
            cache_nested.mkdir(parents=True, exist_ok=True)
            (cache_nested / 'chunk.bin').write_bytes(b'y' * 2048)
            time.sleep(0.12)
            stop_event.set()
            thread.join(timeout=1.0)
        finally:
            shutil.rmtree(target_dir, ignore_errors=True)
            shutil.rmtree(cache_dir, ignore_errors=True)
        self.assertTrue(any('当前仍在等待远端响应或首个文件' in item for item in messages))
        self.assertTrue(any('新增 目标目录 +4.0 KB / +1 个文件' in item for item in messages))
        self.assertTrue(any('HF 缓存 +2.0 KB / +1 个文件' in item for item in messages))

    def test_ensure_download_worker_stdio_reuses_stdout_when_stderr_is_missing(self) -> None:
        fake_stdout = io.StringIO()
        with patch.object(desktop.sys, 'stdout', fake_stdout), \
             patch.object(desktop.sys, 'stderr', None):
            opened = desktop._ensure_download_worker_stdio(None)
            self.assertEqual(opened, [])
            self.assertIs(desktop.sys.stderr, fake_stdout)

    def test_ensure_download_worker_stdio_creates_log_stream_when_both_missing(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix='omniclip-download-stdio-'))
        log_path = temp_dir / 'worker.log'
        try:
            with patch.object(desktop.sys, 'stdout', None), \
                 patch.object(desktop.sys, 'stderr', None):
                opened = desktop._ensure_download_worker_stdio(log_path)
                try:
                    self.assertEqual(len(opened), 1)
                    self.assertIs(desktop.sys.stdout, desktop.sys.stderr)
                    desktop.sys.stdout.write('hello\n')
                    desktop.sys.stdout.flush()
                finally:
                    for stream in opened:
                        stream.close()
            self.assertIn('hello', log_path.read_text(encoding='utf-8'))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()



