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


