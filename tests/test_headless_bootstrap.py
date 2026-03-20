from pathlib import Path
import os
import shutil
import sys
import unittest
from unittest.mock import patch

from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.errors import ActiveDataRootUnavailableError
from omniclip_rag.headless.bootstrap import RuntimeBundle, apply_runtime_layout_if_needed, create_headless_context


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / '.tmp' / 'test_headless_bootstrap'


class HeadlessBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = patch.dict(
            os.environ,
            {
                'APPDATA': str((TEST_ROOT / 'appdata').resolve()),
                'LOCALAPPDATA': str((TEST_ROOT / 'localappdata').resolve()),
                'TEMP': str((TEST_ROOT / 'temp').resolve()),
                'TMP': str((TEST_ROOT / 'temp').resolve()),
                'USERPROFILE': str((TEST_ROOT / 'profile').resolve()),
                'HOME': str((TEST_ROOT / 'profile').resolve()),
                'OMNICLIP_STRICT_TEST_ROOT': str(TEST_ROOT.resolve()),
                'OMNICLIP_BOOTSTRAP_PATH': str((TEST_ROOT / 'roaming' / 'bootstrap.json').resolve()),
            },
            clear=False,
        )
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def test_apply_runtime_layout_requires_available_data_root(self) -> None:
        missing_root = TEST_ROOT / 'missing'
        with patch.object(sys, 'frozen', True, create=True):
            with self.assertRaises(ActiveDataRootUnavailableError):
                apply_runtime_layout_if_needed(str(missing_root))

    def test_create_headless_context_passes_data_root_to_runtime_update_stage(self) -> None:
        data_root = TEST_ROOT / 'profile'
        paths = ensure_data_paths(str(data_root))
        bundle = RuntimeBundle(
            config=AppConfig(vault_path='', data_root=str(paths.global_root)),
            paths=paths,
            language_code='zh-CN',
            theme_code='system',
            scale_percent=100,
        )
        captured: list[object] = []

        class _FakeService:
            def __init__(self, config, runtime_paths) -> None:
                self.config = config
                self.runtime_paths = runtime_paths

            def close(self) -> None:
                return None

        with patch('omniclip_rag.headless.bootstrap.apply_runtime_layout_if_needed', side_effect=lambda root=None: captured.append(root) or []), \
             patch('omniclip_rag.headless.bootstrap.load_runtime_bundle', return_value=bundle), \
             patch('omniclip_rag.headless.bootstrap.configure_file_logging'), \
             patch('omniclip_rag.headless.bootstrap.OmniClipService', _FakeService):
            context = create_headless_context(data_root=str(data_root), apply_runtime_updates=True)
        try:
            self.assertEqual(captured, [str(data_root)])
            self.assertEqual(str(context.bundle.paths.global_root), str(paths.global_root))
        finally:
            context.close()


if __name__ == '__main__':
    unittest.main()
