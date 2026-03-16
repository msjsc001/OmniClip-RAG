import json
import shutil
import tempfile
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


if __name__ == '__main__':
    unittest.main()



