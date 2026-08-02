from __future__ import annotations

import json
import shutil
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from build import (
    GUI_TARGET,
    SHIBOKEN_PORTABLE_FILES,
    BuildTarget,
    _copy_support_files,
    _prepare_bundled_python,
    _prepare_qt_portability_fallback,
)
from omniclip_rag import __version__


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / '.tmp' / 'test_build_release_support'


class BuildReleaseSupportTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def test_prepare_bundled_python_extracts_runtime_python_into_gui_bundle(self) -> None:
        output_dir = TEST_ROOT / 'Caelune-vtest'
        metadata_dir = output_dir / 'runtime_support'
        metadata_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = metadata_dir / 'bundled_python.json'
        metadata_path.write_text(
            json.dumps(
                {
                    'package_url': 'https://example.invalid/python-test.nupkg',
                    'package_filename': 'python-test.nupkg',
                    'python_executable_relative': 'tools/python.exe',
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )
        fake_package = TEST_ROOT / 'python-test.nupkg'
        with zipfile.ZipFile(fake_package, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('tools/python.exe', 'stub-python')

        target = BuildTarget(
            exe_basename='Caelune',
            spec_path=ROOT / 'Caelune.spec',
            output_name='Caelune-vtest',
            output_dir=output_dir,
            release_zip_path=TEST_ROOT / 'Caelune-vtest.zip',
            support_files={},
            protected_runtime_dir=None,
        )

        def fake_download(url: str, destination: Path) -> None:
            self.assertEqual(url, 'https://example.invalid/python-test.nupkg')
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fake_package, destination)

        with patch('build._download_file', side_effect=fake_download):
            _prepare_bundled_python(target)

        self.assertTrue((output_dir / 'runtime_support' / 'python' / 'tools' / 'python.exe').exists())

    def test_copy_support_files_excludes_local_python_bytecode(self) -> None:
        source_dir = TEST_ROOT / 'runtime_support_source'
        cache_dir = source_dir / '__pycache__'
        cache_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / 'install.py').write_text('print("ok")\n', encoding='utf-8')
        (cache_dir / 'install.cpython-313.pyc').write_bytes(b'local-build-path')
        output_dir = TEST_ROOT / 'Caelune-vtest'
        target = BuildTarget(
            exe_basename='Caelune',
            spec_path=ROOT / 'Caelune.spec',
            output_name='Caelune-vtest',
            output_dir=output_dir,
            release_zip_path=TEST_ROOT / 'Caelune-vtest.zip',
            support_files={source_dir: output_dir / 'runtime_support'},
            protected_runtime_dir=None,
        )

        _copy_support_files(target)

        self.assertTrue((output_dir / 'runtime_support' / 'install.py').exists())
        self.assertFalse((output_dir / 'runtime_support' / '__pycache__').exists())

    def test_prepare_qt_portability_fallback_copies_required_shiboken_files(self) -> None:
        output_dir = TEST_ROOT / 'Caelune-vtest'
        source_dir = output_dir / '_internal' / 'shiboken6'
        source_dir.mkdir(parents=True, exist_ok=True)
        for name in SHIBOKEN_PORTABLE_FILES:
            (source_dir / name).write_bytes(f'test-{name}'.encode('utf-8'))
        target = BuildTarget(
            exe_basename='Caelune',
            spec_path=ROOT / 'Caelune.spec',
            output_name='Caelune-vtest',
            output_dir=output_dir,
            release_zip_path=TEST_ROOT / 'Caelune-vtest.zip',
            support_files={},
            protected_runtime_dir=None,
        )

        _prepare_qt_portability_fallback(target)

        fallback = output_dir / 'runtime_support' / 'qt_fallback' / 'shiboken6'
        for name in SHIBOKEN_PORTABLE_FILES:
            self.assertEqual((fallback / name).read_bytes(), f'test-{name}'.encode('utf-8'))

    def test_current_release_metadata_uses_package_version(self) -> None:
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        server = json.loads((ROOT / 'server.json').read_text(encoding='utf-8'))
        self.assertEqual(project['project']['version'], __version__)
        self.assertEqual(server['version'], __version__)
        self.assertEqual(
            GUI_TARGET.release_zip_path.name,
            f'Caelune-v{__version__}-WIN-EXE.zip',
        )
        self.assertIn(f'/v{__version__}/', server['packages'][0]['identifier'])
        self.assertIn(f'-v{__version__}.mcpb', server['packages'][0]['identifier'])
        self.assertIn(
            'github/v/release/EllisMorrow/Caelune',
            (ROOT / 'README.md').read_text(encoding='utf-8'),
        )
        for readme_name in ('README.md', 'README.zh-CN.md'):
            self.assertIn(
                f'version-v{__version__}-',
                (ROOT / readme_name).read_text(encoding='utf-8'),
            )


if __name__ == '__main__':
    unittest.main()
