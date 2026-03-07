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
        self.assertEqual(paths.root, CUSTOM_ROOT.resolve())
        self.assertTrue(paths.sqlite_file.parent.exists())

    def test_falls_back_when_default_root_is_not_writable(self) -> None:
        blocked = Path('blocked_root')
        original = config_module._create_data_paths

        def side_effect(root: Path):
            if root == blocked:
                raise PermissionError('denied')
            if root.name == 'local_appdata':
                return original(FALLBACK_ROOT.resolve())
            return original(root)

        with patch.object(config_module, 'default_data_root', return_value=blocked), \
             patch.dict(config_module.os.environ, {'LOCALAPPDATA': ''}, clear=False), \
             patch.object(config_module, '_create_data_paths', side_effect=side_effect):
            paths = config_module.ensure_data_paths()

        self.assertEqual(paths.root, FALLBACK_ROOT.resolve())
        self.assertTrue(paths.config_file.parent.exists())


if __name__ == '__main__':
    unittest.main()
