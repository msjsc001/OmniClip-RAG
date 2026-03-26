from __future__ import annotations

import json
import shutil
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from build import BuildTarget, _prepare_bundled_python


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / '.tmp' / 'test_build_release_support'


class BuildReleaseSupportTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def test_prepare_bundled_python_extracts_runtime_python_into_gui_bundle(self) -> None:
        output_dir = TEST_ROOT / 'OmniClipRAG-vtest'
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
            exe_basename='OmniClipRAG',
            spec_path=ROOT / 'OmniClipRAG.spec',
            output_name='OmniClipRAG-vtest',
            output_dir=output_dir,
            release_zip_path=TEST_ROOT / 'OmniClipRAG-vtest.zip',
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


if __name__ == '__main__':
    unittest.main()
