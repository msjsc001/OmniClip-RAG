from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from omniclip_rag.app_logging import install_exception_logging
from omniclip_rag.config import default_bootstrap_root, default_data_root, legacy_default_data_root
from omniclip_rag.data_root_bootstrap import (
    BOOTSTRAP_FILENAME,
    BOOTSTRAP_PATH_ENV,
    bootstrap_file_path,
    known_data_roots,
    read_bootstrap_pointer,
    resolve_active_data_root,
    resolve_and_validate_active_data_root,
    validate_active_data_root,
    write_bootstrap_pointer,
)
from omniclip_rag.errors import ActiveDataRootUnavailableError


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / '.tmp' / 'test_bootstrap_root'


class DataRootBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = patch.dict(
            os.environ,
            {
                'APPDATA': str(TEST_ROOT / 'appdata'),
                'LOCALAPPDATA': str(TEST_ROOT / 'localappdata'),
                'TEMP': str(TEST_ROOT / 'temp'),
                'TMP': str(TEST_ROOT / 'temp'),
                'USERPROFILE': str(TEST_ROOT / 'profile'),
                'HOME': str(TEST_ROOT / 'profile'),
                'OMNICLIP_STRICT_TEST_ROOT': str(TEST_ROOT.resolve()),
                BOOTSTRAP_PATH_ENV: str((TEST_ROOT / 'roaming' / BOOTSTRAP_FILENAME).resolve()),
            },
            clear=False,
        )
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def test_default_data_root_uses_rag_default_suffix(self) -> None:
        self.assertEqual(default_data_root(), (TEST_ROOT / 'appdata' / 'OmniClip RAG-default').resolve())

    def test_bootstrap_root_stays_on_appdata_omniclip_rag(self) -> None:
        self.assertEqual(default_bootstrap_root(), (TEST_ROOT / 'appdata' / 'OmniClip RAG').resolve())

    def test_write_and_read_bootstrap_pointer_roundtrip(self) -> None:
        data_root = TEST_ROOT / 'profiles' / 'alpha'
        pointer = write_bootstrap_pointer(data_root, known_data_roots=[TEST_ROOT / 'profiles' / 'beta'])
        stored = read_bootstrap_pointer()
        self.assertEqual(pointer, TEST_ROOT.resolve() / 'roaming' / BOOTSTRAP_FILENAME)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored['schema_version'], 2)
        self.assertEqual(stored['active_data_root'], str(data_root.resolve()))
        self.assertEqual(
            stored['known_data_roots'],
            [str(data_root.resolve()), str((TEST_ROOT / 'profiles' / 'beta').resolve())],
        )

    def test_resolve_active_data_root_prefers_bootstrap_pointer(self) -> None:
        data_root = TEST_ROOT / 'profiles' / 'beta'
        data_root.mkdir(parents=True, exist_ok=True)
        write_bootstrap_pointer(data_root, known_data_roots=[TEST_ROOT / 'profiles' / 'alpha'])
        resolved = resolve_active_data_root()
        self.assertEqual(resolved.path, data_root.resolve())
        self.assertEqual(resolved.source, 'bootstrap')
        self.assertEqual(
            list(resolved.known_data_roots),
            [str(data_root.resolve()), str((TEST_ROOT / 'profiles' / 'alpha').resolve())],
        )

    def test_validate_active_data_root_rejects_missing_explicit_target(self) -> None:
        missing_root = TEST_ROOT / 'missing'
        resolved = resolve_active_data_root(explicit_data_root=missing_root)
        self.assertEqual(resolved.source, 'explicit')
        with self.assertRaises(ActiveDataRootUnavailableError) as ctx:
            validate_active_data_root(resolved)
        self.assertEqual(ctx.exception.reason, 'active_data_root_missing')

    def test_default_source_allows_first_run_nonexistent_directory(self) -> None:
        resolved = resolve_active_data_root()
        validated = validate_active_data_root(resolved)
        self.assertEqual(validated.path, (TEST_ROOT / 'appdata' / 'OmniClip RAG-default').resolve())
        self.assertEqual(validated.source, 'default')

    def test_known_data_roots_returns_active_first(self) -> None:
        first = TEST_ROOT / 'profiles' / 'alpha'
        second = TEST_ROOT / 'profiles' / 'beta'
        first.mkdir(parents=True, exist_ok=True)
        second.mkdir(parents=True, exist_ok=True)
        write_bootstrap_pointer(first, known_data_roots=[second, first])
        self.assertEqual(known_data_roots(), [str(first.resolve()), str(second.resolve())])

    def test_known_data_roots_includes_existing_legacy_default_environment(self) -> None:
        legacy_root = legacy_default_data_root()
        legacy_root.mkdir(parents=True, exist_ok=True)
        roots = known_data_roots()
        self.assertEqual(roots[0], str(default_data_root()))
        self.assertIn(str(legacy_root.resolve()), roots)

    def test_resolve_and_validate_active_data_root_accepts_existing_explicit_directory(self) -> None:
        explicit_root = TEST_ROOT / 'profiles' / 'gamma'
        explicit_root.mkdir(parents=True, exist_ok=True)
        resolved = resolve_and_validate_active_data_root(explicit_root)
        self.assertEqual(resolved.path, explicit_root.resolve())
        self.assertEqual(resolved.source, 'explicit')

    def test_install_exception_logging_without_paths_leaves_no_file_log(self) -> None:
        install_exception_logging()
        self.assertFalse((default_bootstrap_root() / 'shared' / 'logs').exists())
        self.assertFalse((default_data_root() / 'shared' / 'logs').exists())


if __name__ == '__main__':
    unittest.main()
