from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / 'runtime_support' / 'install_runtime_driver.py'
TEST_ROOT = ROOT / '.tmp' / 'test_runtime_install_driver'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _build_pure_python_wheel(repo_dir: Path, *, package_name: str, import_name: str, version: str) -> Path:
    repo_dir.mkdir(parents=True, exist_ok=True)
    normalized_name = package_name.replace('-', '_')
    wheel_name = f'{normalized_name}-{version}-py3-none-any.whl'
    wheel_path = repo_dir / wheel_name
    dist_info = f'{normalized_name}-{version}.dist-info'
    init_body = f"__version__ = '{version}'\nVALUE = 'ok'\n"
    metadata = (
        'Metadata-Version: 2.1\n'
        f'Name: {package_name}\n'
        f'Version: {version}\n'
        'Summary: test wheel\n'
    )
    wheel = (
        'Wheel-Version: 1.0\n'
        'Generator: unittest\n'
        'Root-Is-Purelib: true\n'
        'Tag: py3-none-any\n'
    )
    record = '\n'.join(
        [
            f'{import_name}/__init__.py,,',
            f'{dist_info}/METADATA,,',
            f'{dist_info}/WHEEL,,',
            f'{dist_info}/top_level.txt,,',
            f'{dist_info}/RECORD,,',
        ]
    )
    with zipfile.ZipFile(wheel_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f'{import_name}/__init__.py', init_body)
        archive.writestr(f'{dist_info}/METADATA', metadata)
        archive.writestr(f'{dist_info}/WHEEL', wheel)
        archive.writestr(f'{dist_info}/top_level.txt', f'{import_name}\n')
        archive.writestr(f'{dist_info}/RECORD', record)
    return wheel_path


class RuntimeInstallDriverTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def _run_driver(self, manifest_path: Path, *, source: str = 'official') -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
        runtime_root = TEST_ROOT / 'runtime'
        payload_target = runtime_root / 'components' / 'semantic-core-test'
        wheelhouse = runtime_root / '_downloads' / 'cpu' / 'semantic-core'
        diagnostics_path = TEST_ROOT / 'logs' / 'runtime' / 'runtime-install.json'
        result_path = TEST_ROOT / 'logs' / 'runtime' / 'runtime-install.result.json'
        command = [
            sys.executable,
            str(DRIVER),
            '--manifest',
            str(manifest_path),
            '--profile',
            'cpu',
            '--component',
            'semantic-core',
            '--source',
            source,
            '--runtime-root',
            str(runtime_root),
            '--payload-target',
            str(payload_target),
            '--wheelhouse',
            str(wheelhouse),
            '--diagnostics-path',
            str(diagnostics_path),
            '--result-path',
            str(result_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        return completed, payload_target, diagnostics_path, result_path

    def test_driver_installs_runtime_from_local_find_links_manifest(self) -> None:
        local_repo = TEST_ROOT / 'repo'
        _build_pure_python_wheel(local_repo, package_name='demo-pkg', import_name='demo_pkg', version='1.0.0')
        manifest_path = TEST_ROOT / 'manifest.json'
        manifest_path.write_text(
            json.dumps(
                {
                    'schema_version': 2,
                    'profile': 'cpu',
                    'component': 'semantic-core',
                    'python_tag': 'cp313',
                    'platform_tag': 'win_amd64',
                    'requirements': [
                        {
                            'name': 'demo-pkg',
                            'version': '1.0.0',
                            'requirement': 'demo-pkg==1.0.0',
                            'source_key': 'local',
                        }
                    ],
                    'artifacts': [
                        {
                            'name': 'demo-pkg',
                            'version': '1.0.0',
                            'requirement': 'demo-pkg==1.0.0',
                            'filename': 'demo_pkg-1.0.0-py3-none-any.whl',
                            'sha256': _sha256(local_repo / 'demo_pkg-1.0.0-py3-none-any.whl'),
                            'source_key': 'local',
                        }
                    ],
                    'source_profiles': {
                        'official': {
                            'local': {
                                'find_links': [str(local_repo)],
                            }
                        },
                        'mirror': {
                            'local': {
                                'find_links': [str(local_repo)],
                            }
                        },
                    },
                    'cleanup_patterns': ['demo_pkg', 'demo_pkg-1.0.0.dist-info'],
                    'required_modules': ['demo_pkg'],
                    'validation_probes': ['demo_pkg'],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )

        completed, payload_target, diagnostics_path, result_path = self._run_driver(manifest_path)
        self.assertEqual(completed.returncode, 0, msg=completed.stdout + '\n' + completed.stderr)
        self.assertTrue((payload_target / 'demo_pkg' / '__init__.py').exists())
        self.assertTrue((payload_target / '_runtime_bootstrap.json').exists())
        self.assertTrue((payload_target / '_runtime_validation.json').exists())
        self.assertTrue((payload_target / '_runtime_manifest.json').exists())
        diagnostics = json.loads(diagnostics_path.read_text(encoding='utf-8'))
        result = json.loads(result_path.read_text(encoding='utf-8'))
        self.assertEqual(diagnostics['status'], 'ok')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['selected_source'], 'official')
        artifact_names = {item['filename'] for item in result['downloaded_artifacts']}
        self.assertIn('demo_pkg-1.0.0-py3-none-any.whl', artifact_names)
        self.assertIn('[下载 1/1]', completed.stdout)
        self.assertIn('Runtime component was installed successfully.', completed.stdout)
        self.assertNotIn('"status": "ok"', completed.stdout)

    def test_driver_falls_back_to_mirror_source_when_primary_source_is_empty(self) -> None:
        local_repo = TEST_ROOT / 'mirror_repo'
        empty_repo = TEST_ROOT / 'empty_repo'
        empty_repo.mkdir(parents=True, exist_ok=True)
        _build_pure_python_wheel(local_repo, package_name='mirror-demo', import_name='mirror_demo', version='1.0.0')
        manifest_path = TEST_ROOT / 'fallback_manifest.json'
        manifest_path.write_text(
            json.dumps(
                {
                    'schema_version': 2,
                    'profile': 'cpu',
                    'component': 'semantic-core',
                    'python_tag': 'cp313',
                    'platform_tag': 'win_amd64',
                    'requirements': [
                        {
                            'name': 'mirror-demo',
                            'version': '1.0.0',
                            'requirement': 'mirror-demo==1.0.0',
                            'source_key': 'local',
                        }
                    ],
                    'artifacts': [
                        {
                            'name': 'mirror-demo',
                            'version': '1.0.0',
                            'requirement': 'mirror-demo==1.0.0',
                            'filename': 'mirror_demo-1.0.0-py3-none-any.whl',
                            'sha256': _sha256(local_repo / 'mirror_demo-1.0.0-py3-none-any.whl'),
                            'source_key': 'local',
                        }
                    ],
                    'source_profiles': {
                        'official': {
                            'local': {
                                'find_links': [str(empty_repo)],
                            }
                        },
                        'mirror': {
                            'local': {
                                'find_links': [str(local_repo)],
                            }
                        },
                    },
                    'cleanup_patterns': ['mirror_demo', 'mirror_demo-1.0.0.dist-info'],
                    'required_modules': ['mirror_demo'],
                    'validation_probes': ['mirror_demo'],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )

        completed, payload_target, diagnostics_path, result_path = self._run_driver(manifest_path, source='official')
        self.assertEqual(completed.returncode, 0, msg=completed.stdout + '\n' + completed.stderr)
        self.assertTrue((payload_target / 'mirror_demo' / '__init__.py').exists())
        diagnostics = json.loads(diagnostics_path.read_text(encoding='utf-8'))
        result = json.loads(result_path.read_text(encoding='utf-8'))
        self.assertEqual(diagnostics['status'], 'ok')
        self.assertEqual(result['selected_source'], 'mirror')
        attempts = diagnostics.get('download_attempts') or []
        self.assertGreaterEqual(len(attempts), 2)

    def test_driver_repairs_one_package_by_inheriting_existing_semantic_runtime(self) -> None:
        local_repo = TEST_ROOT / 'repair_repo'
        patch_wheel = _build_pure_python_wheel(
            local_repo,
            package_name='patch-demo',
            import_name='patch_demo',
            version='2.0.0',
        )
        runtime_root = TEST_ROOT / 'runtime'
        live_root = runtime_root / 'components' / 'semantic-core-live'
        (live_root / 'base_demo').mkdir(parents=True, exist_ok=True)
        (live_root / 'base_demo' / '__init__.py').write_text("VALUE = 'preserved'\n", encoding='utf-8')
        (live_root / 'patch_demo').mkdir(parents=True, exist_ok=True)
        (live_root / 'patch_demo' / '__init__.py').write_text("__version__ = '1.0.0'\n", encoding='utf-8')
        (live_root / 'patch_demo-1.0.0.dist-info').mkdir(parents=True, exist_ok=True)
        (live_root / '_runtime_validation.json').write_text('{"validated":true}', encoding='utf-8')
        (runtime_root / '_runtime_components.json').write_text(
            json.dumps({'semantic-core': {'path': str(live_root)}}),
            encoding='utf-8',
        )
        manifest_path = TEST_ROOT / 'repair_manifest.json'
        patch_artifact = {
            'name': 'patch-demo',
            'version': '2.0.0',
            'requirement': 'patch-demo==2.0.0',
            'filename': patch_wheel.name,
            'sha256': _sha256(patch_wheel),
            'source_key': 'local',
        }
        manifest_path.write_text(
            json.dumps(
                {
                    'schema_version': 2,
                    'profile': 'cpu',
                    'component': 'semantic-core',
                    'requirements': [
                        {'name': 'base-demo', 'version': '1.0.0', 'requirement': 'base-demo==1.0.0', 'source_key': 'local'},
                        {'name': 'patch-demo', 'version': '2.0.0', 'requirement': 'patch-demo==2.0.0', 'source_key': 'local'},
                    ],
                    'artifacts': [patch_artifact],
                    'source_profiles': {
                        'official': {'local': {'find_links': [str(local_repo)]}},
                        'mirror': {'local': {'find_links': [str(local_repo)]}},
                    },
                    'cleanup_patterns': ['base_demo', 'patch_demo', '*-demo-*.dist-info'],
                    'required_modules': ['base_demo', 'patch_demo'],
                    'validation_probes': ['base_demo', 'patch_demo'],
                    'repair': {
                        'inherit_component': 'semantic-core',
                        'require_validated_existing': True,
                        'minimum_existing_modules': ['base_demo', 'patch_demo'],
                        'cleanup_patterns': ['patch_demo', 'patch_demo-*.dist-info'],
                        'artifacts': [patch_artifact],
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )

        completed, payload_target, diagnostics_path, result_path = self._run_driver(manifest_path)

        self.assertEqual(completed.returncode, 0, msg=completed.stdout + '\n' + completed.stderr)
        self.assertEqual((payload_target / 'base_demo' / '__init__.py').read_text(encoding='utf-8'), "VALUE = 'preserved'\n")
        self.assertIn("2.0.0", (payload_target / 'patch_demo' / '__init__.py').read_text(encoding='utf-8'))
        self.assertFalse((payload_target / 'patch_demo-1.0.0.dist-info').exists())
        diagnostics = json.loads(diagnostics_path.read_text(encoding='utf-8'))
        result = json.loads(result_path.read_text(encoding='utf-8'))
        self.assertEqual(diagnostics['install_mode'], 'repair')
        self.assertEqual(result['install_mode'], 'repair')
        self.assertEqual(
            [item['filename'] for item in result['downloaded_artifacts']],
            [patch_wheel.name],
        )


if __name__ == '__main__':
    unittest.main()
