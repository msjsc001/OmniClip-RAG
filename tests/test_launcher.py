from __future__ import annotations

import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

import launcher


class LauncherBootstrapTests(unittest.TestCase):
    def test_collect_bundle_dll_dirs_includes_vendored_qt_locations(self) -> None:
        bundle_root = Path('C:/bundle')
        payload_root = bundle_root / '_internal'
        runtime_dir = bundle_root / 'runtime'

        dll_dirs = launcher._collect_bundle_dll_dirs(
            bundle_root=bundle_root,
            payload_root=payload_root,
            runtime_dir=runtime_dir,
            extra_dll_paths=[],
        )

        self.assertIn(payload_root / '.vendor' / 'PySide6', dll_dirs)
        self.assertIn(payload_root / '.vendor' / 'PySide6' / 'plugins', dll_dirs)
        self.assertIn(payload_root / '.vendor' / 'shiboken6', dll_dirs)
        self.assertIn(bundle_root / '.vendor' / 'PySide6', dll_dirs)
        self.assertIn(bundle_root / '.vendor' / 'shiboken6', dll_dirs)

    def test_runtime_bootstrap_paths_only_returns_dll_dir(self) -> None:
        runtime_dir = Path(self._testMethodName).resolve()
        if runtime_dir.exists():
            import shutil
            shutil.rmtree(runtime_dir, ignore_errors=True)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        dll_dir = runtime_dir / 'DLLs'
        dll_dir.mkdir(parents=True, exist_ok=True)
        marker = runtime_dir / '_runtime_bootstrap.json'
        marker.write_text(json.dumps({
            'stdlib': 'C:/Python313/Lib',
            'platstdlib': 'C:/Python313/Lib',
            'dll_dir': str(dll_dir),
        }), encoding='utf-8')
        try:
            sys_paths, dll_paths = launcher._runtime_bootstrap_paths(runtime_dir)
            self.assertEqual(sys_paths, [])
            self.assertEqual(dll_paths, [dll_dir])
        finally:
            if runtime_dir.exists():
                import shutil
                shutil.rmtree(runtime_dir, ignore_errors=True)

    def test_apply_pending_runtime_updates_promotes_payload_into_component_runtime(self) -> None:
        runtime_dir = Path(self._testMethodName).resolve()
        if runtime_dir.exists():
            import shutil
            shutil.rmtree(runtime_dir, ignore_errors=True)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        try:
            (runtime_dir / 'components' / 'semantic-core' / 'torch').mkdir(parents=True, exist_ok=True)
            (runtime_dir / 'components' / 'semantic-core' / 'torch' / 'old.txt').write_text('old', encoding='utf-8')
            pending_root = runtime_dir / '.pending' / 'compute-core'
            payload_dir = pending_root / 'payload'
            (payload_dir / 'torch').mkdir(parents=True, exist_ok=True)
            (payload_dir / 'torch' / 'new.txt').write_text('new', encoding='utf-8')
            (pending_root / 'manifest.json').write_text(json.dumps({
                'component': 'compute-core',
                'payload_dir': str(payload_dir),
                'cleanup_patterns': ['torch'],
            }), encoding='utf-8')

            launcher._apply_pending_runtime_updates(runtime_dir)

            self.assertTrue((runtime_dir / 'components' / 'semantic-core' / 'torch' / 'new.txt').exists())
            self.assertFalse((runtime_dir / 'components' / 'semantic-core' / 'torch' / 'old.txt').exists())
            self.assertFalse((runtime_dir / '.pending' / 'compute-core').exists())
        finally:
            if runtime_dir.exists():
                import shutil
                shutil.rmtree(runtime_dir, ignore_errors=True)

    def test_apply_pending_runtime_updates_normalizes_legacy_component_roots(self) -> None:
        runtime_dir = Path((self._testMethodName + '_components')).resolve()
        if runtime_dir.exists():
            import shutil
            shutil.rmtree(runtime_dir, ignore_errors=True)
        (runtime_dir / 'components' / 'model-stack' / 'transformers').mkdir(parents=True, exist_ok=True)
        (runtime_dir / 'components' / 'model-stack' / 'transformers' / '__init__.py').write_text('', encoding='utf-8')
        (runtime_dir / 'components' / 'semantic-core' / 'torch').mkdir(parents=True, exist_ok=True)
        (runtime_dir / 'components' / 'semantic-core' / 'torch' / '__init__.py').write_text('', encoding='utf-8')
        try:
            launcher._apply_pending_runtime_updates(runtime_dir)
            self.assertTrue((runtime_dir / 'components' / 'semantic-core' / 'torch' / '__init__.py').exists())
            self.assertTrue((runtime_dir / 'components' / 'semantic-core' / 'transformers' / '__init__.py').exists())
            self.assertFalse((runtime_dir / 'components' / 'model-stack').exists())
        finally:
            if runtime_dir.exists():
                import shutil
                shutil.rmtree(runtime_dir, ignore_errors=True)

    def test_bootstrap_local_packages_does_not_apply_pending_runtime_updates_before_ui(self) -> None:
        bundle_root = Path(self._testMethodName).resolve()
        if bundle_root.exists():
            import shutil
            shutil.rmtree(bundle_root, ignore_errors=True)
        bundle_root.mkdir(parents=True, exist_ok=True)
        try:
            with patch('launcher._runtime_bootstrap_paths', return_value=([], [])) as bootstrap_mock, patch('launcher._register_dll_directories'):
                with patch.object(sys, 'frozen', False, create=True), patch.object(launcher, '__file__', str(bundle_root / 'launcher.py')):
                    launcher._bootstrap_local_packages()
            bootstrap_mock.assert_called_once()
        finally:
            if bundle_root.exists():
                import shutil
                shutil.rmtree(bundle_root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
