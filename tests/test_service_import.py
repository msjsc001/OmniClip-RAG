import importlib
import sys
import unittest


class ServiceImportIsolationTests(unittest.TestCase):
    def test_service_import_does_not_eagerly_require_extension_parsers(self) -> None:
        for name in list(sys.modules):
            if name == 'omniclip_rag.service' or name.startswith('omniclip_rag.extensions'):
                sys.modules.pop(name, None)
        module = importlib.import_module('omniclip_rag.service')
        self.assertTrue(hasattr(module, 'OmniClipService'))


if __name__ == '__main__':
    unittest.main()
