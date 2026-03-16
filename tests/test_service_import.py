import importlib
import sys
import unittest


class ServiceImportIsolationTests(unittest.TestCase):
    def test_service_import_does_not_eagerly_require_extension_parsers(self) -> None:
        # This test temporarily clears selected modules to validate import isolation.
        # Important: we must restore the original module objects afterwards, otherwise
        # later tests that already imported those modules during collection will hold
        # references to stale globals and patching by module path will break.
        removed: dict[str, object] = {}
        target_prefixes = ('omniclip_rag.extensions',)
        target_names = {'omniclip_rag.service'}

        for name in list(sys.modules):
            if name in target_names or any(name.startswith(prefix) for prefix in target_prefixes):
                existing = sys.modules.pop(name, None)
                if existing is not None:
                    removed[name] = existing
        try:
            module = importlib.import_module('omniclip_rag.service')
            self.assertTrue(hasattr(module, 'OmniClipService'))
        finally:
            # Drop any newly imported versions of the modules we evicted, then restore
            # the original ones so subsequent tests see a consistent import graph.
            for name in list(sys.modules):
                if name in removed:
                    sys.modules.pop(name, None)
            sys.modules.update(removed)


if __name__ == '__main__':
    unittest.main()
