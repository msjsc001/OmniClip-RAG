from pathlib import Path
import os
import shutil
import unittest
from unittest.mock import patch

from omniclip_rag import config as config_module


ROOT = Path(__file__).resolve().parents[1]
CUSTOM_ROOT = ROOT / '.tmp' / 'config_custom'


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = patcher = patch.dict(
            os.environ,
            {
                'APPDATA': str((ROOT / '.tmp' / 'config_appdata').resolve()),
                'LOCALAPPDATA': str((ROOT / '.tmp' / 'config_localappdata').resolve()),
                'TEMP': str((ROOT / '.tmp' / 'config_temp').resolve()),
                'TMP': str((ROOT / '.tmp' / 'config_temp').resolve()),
                'USERPROFILE': str((ROOT / '.tmp' / 'config_profile').resolve()),
                'HOME': str((ROOT / '.tmp' / 'config_profile').resolve()),
                'OMNICLIP_STRICT_TEST_ROOT': str((ROOT / '.tmp').resolve()),
            },
            clear=False,
        )
        patcher.start()

    def tearDown(self) -> None:
        self._env.stop()
        for path in (
            ROOT / '.tmp' / 'config_appdata',
            ROOT / '.tmp' / 'config_localappdata',
            ROOT / '.tmp' / 'config_temp',
            ROOT / '.tmp' / 'config_profile',
            CUSTOM_ROOT,
        ):
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
        initial_paths = config_module.ensure_data_paths(str(CUSTOM_ROOT), str(vault))
        legacy_workspace = initial_paths.root
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

    def test_default_data_root_uses_rag_default_suffix(self) -> None:
        self.assertEqual(
            config_module.default_data_root(),
            (ROOT / '.tmp' / 'config_appdata' / 'OmniClip RAG-default').resolve(),
        )

    def test_probe_data_root_distinguishes_new_invalid_and_existing_states(self) -> None:
        empty_root = CUSTOM_ROOT / 'empty_root'
        empty_root.mkdir(parents=True, exist_ok=True)
        file_root = CUSTOM_ROOT / 'file_root'
        file_root.parent.mkdir(parents=True, exist_ok=True)
        file_root.write_text('not a directory', encoding='utf-8')
        plain_root = CUSTOM_ROOT / 'plain_root'
        plain_root.mkdir(parents=True, exist_ok=True)
        (plain_root / 'notes.txt').write_text('plain directory', encoding='utf-8')
        broken_root = CUSTOM_ROOT / 'broken_root'
        broken_root.mkdir(parents=True, exist_ok=True)
        (broken_root / 'shared').mkdir(parents=True, exist_ok=True)
        (broken_root / 'config.json').write_text('{}', encoding='utf-8')
        legacy_root = CUSTOM_ROOT / 'legacy_root'
        legacy_root.mkdir(parents=True, exist_ok=True)
        (legacy_root / 'shared').mkdir(parents=True, exist_ok=True)
        (legacy_root / 'workspaces').mkdir(parents=True, exist_ok=True)
        (legacy_root / 'config.json').write_text('{}', encoding='utf-8')

        self.assertEqual(config_module.probe_data_root(empty_root, allow_create=True).state, 'new')
        self.assertEqual(config_module.probe_data_root(file_root, allow_create=True).state, 'invalid_not_directory')
        self.assertEqual(config_module.probe_data_root(plain_root, allow_create=True).state, 'invalid_not_environment')
        self.assertEqual(config_module.probe_data_root(broken_root, allow_create=True).state, 'invalid_broken_environment')
        legacy_probe = config_module.probe_data_root(legacy_root, allow_create=True)
        self.assertEqual(legacy_probe.state, 'existing')
        self.assertTrue(legacy_probe.legacy_environment)

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
