from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from omniclip_rag import config as config_module


ROOT = Path(__file__).resolve().parents[1]
FALLBACK_ROOT = ROOT / '.tmp' / 'config_fallback'
CUSTOM_ROOT = ROOT / '.tmp' / 'config_custom'


class ConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        for path in (FALLBACK_ROOT, CUSTOM_ROOT):
            if path.exists():
                shutil.rmtree(path)

    def test_custom_root_is_respected(self) -> None:
        paths = config_module.ensure_data_paths(str(CUSTOM_ROOT))
        self.assertEqual(paths.global_root, CUSTOM_ROOT.resolve())
        self.assertEqual(paths.shared_root, CUSTOM_ROOT.resolve() / 'shared')
        self.assertEqual(paths.cache_dir, CUSTOM_ROOT.resolve() / 'shared' / 'cache')
        self.assertEqual(paths.logs_dir, CUSTOM_ROOT.resolve() / 'shared' / 'logs')
        self.assertEqual(paths.root, CUSTOM_ROOT.resolve() / 'workspaces' / config_module.DEFAULT_WORKSPACE_ID)
        self.assertTrue(paths.sqlite_file.parent.exists())
        self.assertTrue(paths.config_file.parent.exists())

    def test_workspace_isolated_per_vault(self) -> None:
        vault_a = ROOT / 'vault_a'
        vault_b = ROOT / 'vault_b'
        paths_a = config_module.ensure_data_paths(str(CUSTOM_ROOT), str(vault_a))
        paths_b = config_module.ensure_data_paths(str(CUSTOM_ROOT), str(vault_b))

        self.assertEqual(paths_a.global_root, paths_b.global_root)
        self.assertEqual(paths_a.shared_root, paths_b.shared_root)
        self.assertNotEqual(paths_a.workspace_id, paths_b.workspace_id)
        self.assertNotEqual(paths_a.root, paths_b.root)
        self.assertTrue(str(paths_a.root).startswith(str(CUSTOM_ROOT.resolve() / 'workspaces')))
        self.assertTrue(str(paths_b.root).startswith(str(CUSTOM_ROOT.resolve() / 'workspaces')))

    def test_legacy_workspace_cache_and_logs_migrate_to_shared_area(self) -> None:
        vault = ROOT / 'vault_migrate'
        workspace_id = config_module.workspace_id_for_vault(vault)
        legacy_workspace = CUSTOM_ROOT / 'workspaces' / workspace_id
        legacy_cache_file = legacy_workspace / 'cache' / 'models' / 'stub.bin'
        legacy_log_file = legacy_workspace / 'logs' / 'run.log'
        legacy_cache_file.parent.mkdir(parents=True, exist_ok=True)
        legacy_log_file.parent.mkdir(parents=True, exist_ok=True)
        legacy_cache_file.write_text('cache', encoding='utf-8')
        legacy_log_file.write_text('log', encoding='utf-8')

        paths = config_module.ensure_data_paths(str(CUSTOM_ROOT), str(vault))

        self.assertTrue((paths.cache_dir / 'models' / 'stub.bin').exists())
        self.assertTrue((paths.logs_dir / 'run.log').exists())
        self.assertFalse((legacy_workspace / 'cache').exists())
        self.assertFalse((legacy_workspace / 'logs').exists())

    def test_falls_back_when_default_root_is_not_writable(self) -> None:
        blocked = Path('blocked_root')
        original = config_module._create_data_paths

        def side_effect(root: Path, vault_path=None):
            if root == blocked:
                raise PermissionError('denied')
            if root.name == 'local_appdata':
                return original(FALLBACK_ROOT.resolve(), vault_path=vault_path)
            return original(root, vault_path=vault_path)

        with patch.object(config_module, 'default_data_root', return_value=blocked), \
             patch.dict(config_module.os.environ, {'LOCALAPPDATA': ''}, clear=False), \
             patch.object(config_module, '_create_data_paths', side_effect=side_effect):
            paths = config_module.ensure_data_paths()

        self.assertEqual(paths.global_root, FALLBACK_ROOT.resolve())
        self.assertTrue(paths.config_file.parent.exists())


if __name__ == '__main__':
    unittest.main()
