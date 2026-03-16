from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
import unittest

from omniclip_rag.vector_index import detect_acceleration, inspect_runtime_environment, runtime_component_status

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_runtime.ps1"
TEST_ROOT = ROOT / '.tmp' / 'test_runtime_install_script'


class RuntimeInstallScriptTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def test_runtime_install_script_uses_file_based_required_module_validation(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("required-modules.txt", text)
        self.assertIn("required_modules_path = Path(sys.argv[2]).resolve()", text)
        self.assertIn("splitlines()", text)
        self.assertNotIn("json.loads(required_modules_path.read_text", text)
        self.assertNotIn("json.loads(sys.argv[2])", text)

    def test_runtime_install_script_writes_manifest_without_bom_serializer_dependency(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("$utf8NoBom = New-Object System.Text.UTF8Encoding($false)", text)
        self.assertIn("[System.IO.File]::WriteAllText($requiredModulesPath, (($requiredModules | Where-Object { $_ }) -join [Environment]::NewLine), $utf8NoBom)", text)
        self.assertNotIn("System.Text.Json.JsonSerializer", text)

    def test_runtime_probe_ignores_installer_stdlib_entries_from_bootstrap_metadata(self) -> None:
        app_root = TEST_ROOT / 'bootstrap_ignore_stdlib' / 'app'
        runtime_dir = app_root / 'runtime'
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / '_runtime_bootstrap.json').write_text(json.dumps({
            'stdlib': r'C:\Python313\Lib',
            'platstdlib': r'C:\Python313\DLLs',
            'dll_dir': '',
        }), encoding='utf-8')

        def write_pkg(base: Path, name: str, body: str = '') -> None:
            package_dir = base / name
            package_dir.mkdir(parents=True, exist_ok=True)
            (package_dir / '__init__.py').write_text(body or '', encoding='utf-8')

        write_pkg(runtime_dir, 'torch', 'class cuda:\n    @staticmethod\n    def is_available():\n        return False\n\n__version__ = "2.10.0"\n')
        write_pkg(runtime_dir, 'sentence_transformers', 'from transformers.configuration_utils import PretrainedConfig\n')
        (runtime_dir / 'transformers' / 'utils').mkdir(parents=True, exist_ok=True)
        write_pkg(runtime_dir, 'transformers', '')
        (runtime_dir / 'transformers' / 'configuration_utils.py').write_text('class PretrainedConfig:\n    pass\n', encoding='utf-8')
        (runtime_dir / 'transformers' / 'utils' / '__init__.py').write_text('', encoding='utf-8')
        write_pkg(runtime_dir, 'huggingface_hub', '')
        (runtime_dir / 'huggingface_hub' / 'hf_api.py').write_text('', encoding='utf-8')
        write_pkg(runtime_dir, 'safetensors', '')
        (runtime_dir / 'numpy' / '_core').mkdir(parents=True, exist_ok=True)
        write_pkg(runtime_dir, 'numpy', '__version__ = "2.0.0"\n')
        (runtime_dir / 'scipy' / 'linalg').mkdir(parents=True, exist_ok=True)
        write_pkg(runtime_dir, 'scipy', '')
        for module_name in ['torch', 'sentence_transformers', 'transformers', 'huggingface_hub', 'safetensors', 'numpy', 'scipy']:
            sys.modules.pop(module_name, None)
        with patch('omniclip_rag.vector_index._application_root_dir', return_value=app_root), \
             patch('omniclip_rag.vector_index._ACCELERATION_CACHE', None), \
             patch('omniclip_rag.vector_index._detect_nvidia_gpus', return_value=[]), \
             patch('omniclip_rag.vector_index._detect_nvcc_version', return_value=''):
            payload = detect_acceleration(force_refresh=True)
        self.assertTrue(payload['torch_available'])
        self.assertTrue(payload['sentence_transformers_available'])

    def test_runtime_install_script_supports_apply_pending_only_helper_flow(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('[switch]$ApplyPendingOnly', text)
        self.assertIn('function Apply-PendingRuntimeUpdates', text)
        self.assertIn('function Start-PendingApplyHelper', text)
        self.assertIn('-ApplyPendingOnly', text)
        self.assertIn('Restart OmniClipRAG.exe after the download finishes.', text)
        self.assertIn('The new runtime component has been registered and the next launch will use it automatically.', text)

    def test_pending_runtime_payload_stays_staged_until_restart(self) -> None:
        app_root = TEST_ROOT / 'pending_detect' / 'app'
        runtime_dir = app_root / 'runtime'
        pending_semantic = runtime_dir / '.pending' / 'semantic-core' / 'payload'
        pending_semantic.mkdir(parents=True, exist_ok=True)

        def write_pkg(base: Path, name: str, body: str = '') -> None:
            package_dir = base / name
            package_dir.mkdir(parents=True, exist_ok=True)
            (package_dir / '__init__.py').write_text(body or '', encoding='utf-8')

        write_pkg(pending_semantic, 'torch', 'class cuda:\n    @staticmethod\n    def is_available():\n        return False\n\n__version__ = "2.10.0"\n')
        write_pkg(pending_semantic, 'sentence_transformers', 'from transformers.configuration_utils import PretrainedConfig\n')
        (pending_semantic / 'transformers' / 'utils').mkdir(parents=True, exist_ok=True)
        write_pkg(pending_semantic, 'transformers', '')
        (pending_semantic / 'transformers' / 'configuration_utils.py').write_text('class PretrainedConfig:\n    pass\n', encoding='utf-8')
        (pending_semantic / 'transformers' / 'utils' / '__init__.py').write_text('', encoding='utf-8')
        write_pkg(pending_semantic, 'huggingface_hub', '')
        (pending_semantic / 'huggingface_hub' / 'hf_api.py').write_text('', encoding='utf-8')
        write_pkg(pending_semantic, 'safetensors', '')
        (pending_semantic / 'numpy' / '_core').mkdir(parents=True, exist_ok=True)
        write_pkg(pending_semantic, 'numpy', '__version__ = "2.0.0"\n')
        (pending_semantic / 'scipy' / 'linalg').mkdir(parents=True, exist_ok=True)
        write_pkg(pending_semantic, 'scipy', '')
        (pending_semantic / '_runtime_bootstrap.json').write_text(json.dumps({'dll_dir': ''}), encoding='utf-8')
        (pending_semantic.parent / 'manifest.json').write_text(json.dumps({
            'component': 'semantic-core',
            'payload_dir': str(pending_semantic),
            'cleanup_patterns': ['torch', 'sentence_transformers', 'transformers', 'huggingface_hub', 'safetensors', 'numpy', 'scipy', '_runtime_bootstrap.json'],
        }), encoding='utf-8')

        for module_name in ['torch', 'sentence_transformers', 'transformers', 'huggingface_hub', 'safetensors', 'numpy', 'scipy']:
            sys.modules.pop(module_name, None)
        with patch('omniclip_rag.vector_index._application_root_dir', return_value=app_root), \
             patch('omniclip_rag.vector_index._ACCELERATION_CACHE', None), \
             patch('omniclip_rag.vector_index._detect_nvidia_gpus', return_value=[]), \
             patch('omniclip_rag.vector_index._detect_nvcc_version', return_value=''):
            runtime_state = inspect_runtime_environment()
            semantic_state = runtime_component_status('semantic-core')
            payload = detect_acceleration(force_refresh=True)
        self.assertTrue(runtime_state['runtime_pending'])
        self.assertEqual(runtime_state['runtime_pending_components'], ['semantic-core'])
        self.assertFalse(runtime_state['runtime_complete'])
        self.assertEqual(semantic_state['status'], 'pending')
        self.assertFalse(semantic_state['ready'])
        self.assertFalse(payload['sentence_transformers_available'])

    def test_apply_pending_only_promotes_fake_runtime_payload_without_downloading(self) -> None:
        app_root = TEST_ROOT / 'app'
        runtime_dir = app_root / 'runtime'
        pending_semantic = runtime_dir / '.pending' / 'semantic-core' / 'payload'
        pending_vector = runtime_dir / '.pending' / 'vector-store' / 'payload'
        pending_semantic.mkdir(parents=True, exist_ok=True)
        pending_vector.mkdir(parents=True, exist_ok=True)
        (app_root / 'OmniClipRAG.exe').write_text('', encoding='utf-8')
        shutil.copy2(SCRIPT, app_root / 'InstallRuntime.ps1')

        def write_pkg(base: Path, name: str, body: str = '') -> None:
            package_dir = base / name
            package_dir.mkdir(parents=True, exist_ok=True)
            (package_dir / '__init__.py').write_text(body or '', encoding='utf-8')

        write_pkg(pending_semantic, 'torch', 'class cuda:\n    @staticmethod\n    def is_available():\n        return False\n\n__version__ = "2.10.0"\n')
        write_pkg(pending_semantic, 'sentence_transformers', 'from transformers.configuration_utils import PretrainedConfig\n')
        (pending_semantic / 'transformers' / 'utils').mkdir(parents=True, exist_ok=True)
        write_pkg(pending_semantic, 'transformers', '')
        (pending_semantic / 'transformers' / 'configuration_utils.py').write_text('class PretrainedConfig:\n    pass\n', encoding='utf-8')
        (pending_semantic / 'transformers' / 'utils' / '__init__.py').write_text('', encoding='utf-8')
        write_pkg(pending_semantic, 'huggingface_hub', '')
        (pending_semantic / 'huggingface_hub' / 'hf_api.py').write_text('', encoding='utf-8')
        write_pkg(pending_semantic, 'safetensors', '')
        (pending_semantic / 'numpy' / '_core').mkdir(parents=True, exist_ok=True)
        write_pkg(pending_semantic, 'numpy', '__version__ = "2.0.0"\n')
        (pending_semantic / 'scipy' / 'linalg').mkdir(parents=True, exist_ok=True)
        write_pkg(pending_semantic, 'scipy', '')
        (pending_semantic / '_runtime_bootstrap.json').write_text(json.dumps({'dll_dir': ''}), encoding='utf-8')
        (pending_semantic.parent / 'manifest.json').write_text(json.dumps({
            'component': 'semantic-core',
            'payload_dir': str(pending_semantic),
            'cleanup_patterns': ['torch', 'sentence_transformers', 'transformers', 'huggingface_hub', 'safetensors', 'numpy', 'scipy', '_runtime_bootstrap.json'],
        }), encoding='utf-8')

        for name in ['lancedb', 'onnxruntime', 'pyarrow', 'pandas']:
            write_pkg(pending_vector, name, '')
        (pending_vector.parent / 'manifest.json').write_text(json.dumps({
            'component': 'vector-store',
            'payload_dir': str(pending_vector),
            'cleanup_patterns': ['lancedb', 'onnxruntime', 'pyarrow', 'pandas'],
        }), encoding='utf-8')

        result = subprocess.run(
            [
                'powershell',
                '-ExecutionPolicy', 'Bypass',
                '-NoProfile',
                '-File', str(app_root / 'InstallRuntime.ps1'),
                '-ApplyPendingOnly',
            ],
            cwd=str(app_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + '\n' + result.stderr)
        self.assertFalse((runtime_dir / '.pending').exists() and any((runtime_dir / '.pending').iterdir()))
        self.assertTrue((runtime_dir / 'components' / 'semantic-core' / 'sentence_transformers').exists())
        self.assertTrue((runtime_dir / 'components' / 'semantic-core' / 'transformers').exists())
        self.assertTrue((runtime_dir / 'components' / 'semantic-core' / 'huggingface_hub').exists())
        self.assertTrue((runtime_dir / 'components' / 'vector-store' / 'lancedb').exists())
        self.assertTrue((runtime_dir / 'components' / 'semantic-core' / '_runtime_bootstrap.json').exists())

        for module_name in ['torch', 'sentence_transformers', 'transformers', 'huggingface_hub', 'safetensors', 'lancedb', 'onnxruntime', 'pyarrow', 'pandas', 'numpy', 'scipy']:
            sys.modules.pop(module_name, None)
        with patch('omniclip_rag.vector_index._application_root_dir', return_value=app_root), \
             patch('omniclip_rag.vector_index._ACCELERATION_CACHE', None), \
             patch('omniclip_rag.vector_index._detect_nvidia_gpus', return_value=[]), \
             patch('omniclip_rag.vector_index._detect_nvcc_version', return_value=''):
            runtime_state = inspect_runtime_environment()
            semantic_state = runtime_component_status('semantic-core')
            vector_state = runtime_component_status('vector-store')
            payload = detect_acceleration(force_refresh=True)
        self.assertTrue(runtime_state['runtime_complete'])
        self.assertEqual(runtime_state['runtime_pending_components'], [])
        self.assertEqual(semantic_state['status'], 'ready')
        self.assertEqual(vector_state['status'], 'ready')
        self.assertTrue(payload['torch_available'])
        self.assertTrue(payload['sentence_transformers_available'])


    def test_componentized_runtime_layout_sanitizes_legacy_semantic_root_without_downloading(self) -> None:
        from omniclip_rag.runtime_layout import ensure_runtime_layout

        app_root = TEST_ROOT / 'sanitize_runtime_root' / 'app'
        runtime_dir = app_root / 'runtime'
        semantic_root = runtime_dir / 'components' / 'semantic-core'
        semantic_root.mkdir(parents=True, exist_ok=True)
        (semantic_root / 'torch').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'torch' / '__init__.py').write_text('__version__ = "2.10.0"\n', encoding='utf-8')
        (semantic_root / 'numpy' / '_core').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'numpy' / '__init__.py').write_text('__version__ = "2.4.3"\n', encoding='utf-8')
        (semantic_root / 'scipy' / 'linalg').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'scipy' / '__init__.py').write_text('', encoding='utf-8')
        (semantic_root / 'sentence_transformers').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'sentence_transformers' / '__init__.py').write_text('', encoding='utf-8')
        (semantic_root / 'transformers' / 'utils').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'transformers' / '__init__.py').write_text('', encoding='utf-8')
        (semantic_root / 'huggingface_hub').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'huggingface_hub' / '__init__.py').write_text('', encoding='utf-8')
        (semantic_root / 'huggingface_hub' / 'hf_api.py').write_text('', encoding='utf-8')
        (semantic_root / 'safetensors').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'safetensors' / '__init__.py').write_text('', encoding='utf-8')

        (runtime_dir / 'numpy').mkdir(parents=True, exist_ok=True)
        (runtime_dir / 'numpy-2.4.3.dist-info').mkdir(parents=True, exist_ok=True)
        (runtime_dir / 'torch').mkdir(parents=True, exist_ok=True)

        ensure_runtime_layout(runtime_dir)

        self.assertFalse((runtime_dir / 'numpy').exists())
        self.assertFalse((runtime_dir / 'numpy-2.4.3.dist-info').exists())
        self.assertFalse((runtime_dir / 'torch').exists())
        self.assertTrue((semantic_root / 'numpy' / '__init__.py').exists())


if __name__ == "__main__":
    unittest.main()
