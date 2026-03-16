from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from omniclip_rag import config as config_module


ROOT = Path(__file__).resolve().parents[1]
FALLBACK_ROOT = ROOT / '.tmp' / 'config_fallback'
TEMP_FALLBACK_ROOT = ROOT / '.tmp' / 'config_temp_fallback'
CUSTOM_ROOT = ROOT / '.tmp' / 'config_custom'


class ConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        for path in (FALLBACK_ROOT, TEMP_FALLBACK_ROOT, CUSTOM_ROOT):
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

    def test_falls_back_to_local_appdata_when_default_root_is_not_writable(self) -> None:
        blocked = Path('blocked_root')
        local_candidate = Path('local_candidate')
        temp_candidate = Path('temp_candidate')
        original = config_module._create_data_paths

        def side_effect(root: Path, vault_path=None):
            if root == blocked:
                raise PermissionError('denied')
            if root == local_candidate:
                return original(FALLBACK_ROOT.resolve(), vault_path=vault_path)
            if root == temp_candidate:
                raise AssertionError('temp fallback should not be used when local appdata is writable')
            return original(root, vault_path=vault_path)

        with patch.object(config_module, 'default_data_root', return_value=blocked), \
             patch.object(config_module, 'default_local_data_root', return_value=local_candidate), \
             patch.object(config_module, 'temp_data_root', return_value=temp_candidate), \
             patch.object(config_module, '_create_data_paths', side_effect=side_effect):
            paths = config_module.ensure_data_paths()

        self.assertEqual(paths.global_root, FALLBACK_ROOT.resolve())
        self.assertTrue(paths.config_file.parent.exists())

    def test_falls_back_to_temp_when_appdata_roots_are_not_writable(self) -> None:
        blocked_roaming = Path('blocked_roaming')
        blocked_local = Path('blocked_local')
        temp_candidate = Path('temp_candidate')
        original = config_module._create_data_paths

        def side_effect(root: Path, vault_path=None):
            if root in {blocked_roaming, blocked_local}:
                raise PermissionError('denied')
            if root == temp_candidate:
                return original(TEMP_FALLBACK_ROOT.resolve(), vault_path=vault_path)
            return original(root, vault_path=vault_path)

        with patch.object(config_module, 'default_data_root', return_value=blocked_roaming), \
             patch.object(config_module, 'default_local_data_root', return_value=blocked_local), \
             patch.object(config_module, 'temp_data_root', return_value=temp_candidate), \
             patch.object(config_module, '_create_data_paths', side_effect=side_effect):
            paths = config_module.ensure_data_paths()

        self.assertEqual(paths.global_root, TEMP_FALLBACK_ROOT.resolve())
        self.assertTrue(paths.config_file.parent.exists())

    def test_ui_preferences_are_normalized_when_saved(self) -> None:
        vault = ROOT / 'vault_ui'
        paths = config_module.ensure_data_paths(str(CUSTOM_ROOT), str(vault))
        config = config_module.AppConfig(
            vault_path=str(vault),
            data_root=str(paths.global_root),
            ui_theme='night',
            ui_scale_percent=240,
        )

        config_module.save_config(config, paths)
        loaded = config_module.load_config(paths)

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.ui_theme, 'dark')
        self.assertEqual(loaded.ui_scale_percent, 200)


    def test_ensure_directory_accepts_existing_directory_after_permission_race(self) -> None:
        target = CUSTOM_ROOT / 'shared'
        target.mkdir(parents=True, exist_ok=True)
        with patch.object(Path, 'mkdir', side_effect=PermissionError('denied')), \
             patch.object(config_module, '_win_directory_exists', return_value=True):
            config_module._ensure_directory(target)

    def test_log_file_size_is_normalized_when_saved(self) -> None:
        vault = ROOT / 'vault_logs'
        paths = config_module.ensure_data_paths(str(CUSTOM_ROOT), str(vault))
        config = config_module.AppConfig(
            vault_path=str(vault),
            data_root=str(paths.global_root),
            log_file_size_mb=999,
            query_trace_logging_enabled=True,
        )

        config_module.save_config(config, paths)
        loaded = config_module.load_config(paths)

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.log_file_size_mb, config_module.LOG_FILE_SIZE_MB_MAX)
        self.assertTrue(loaded.query_trace_logging_enabled)


if __name__ == '__main__':
    unittest.main()
