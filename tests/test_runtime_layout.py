import json
import shutil
import unittest
from pathlib import Path

from omniclip_rag.runtime_layout import (
    normalize_runtime_component_id,
    runtime_component_live_roots,
    runtime_component_registry_path,
    runtime_components_root,
)


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / '.tmp' / 'test_runtime_layout'


class RuntimeLayoutTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def test_registry_absolute_path_is_salvaged_after_runtime_move(self) -> None:
        runtime_dir = TEST_ROOT / 'runtime'
        component_dir = runtime_components_root(runtime_dir) / 'semantic-core-20260101010101000'
        component_dir.mkdir(parents=True, exist_ok=True)

        registry_payload = {
            'semantic-core': {
                'path': r'X:\Old\OmniClipRAG-v0.3.0\runtime\components\semantic-core-20260101010101000',
                'profile': 'cpu',
                'source': 'official',
                'created_at': '2026-01-01T00:00:00Z',
                'validated': True,
            }
        }
        runtime_component_registry_path(runtime_dir).write_text(
            json.dumps(registry_payload, ensure_ascii=True, indent=2),
            encoding='utf-8',
        )

        roots = runtime_component_live_roots(runtime_dir, 'semantic-core')
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0].resolve(), component_dir.resolve())

    def test_component_discovery_picks_latest_versioned_root(self) -> None:
        runtime_dir = TEST_ROOT / 'runtime2'
        root_a = runtime_components_root(runtime_dir) / 'semantic-core-20250101010101000'
        root_b = runtime_components_root(runtime_dir) / 'semantic-core-20260101010101000'
        root_a.mkdir(parents=True, exist_ok=True)
        root_b.mkdir(parents=True, exist_ok=True)

        roots = runtime_component_live_roots(runtime_dir, 'semantic-core')
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0].resolve(), root_b.resolve())

    def test_gpu_acceleration_alias_points_to_semantic_core(self) -> None:
        self.assertEqual(normalize_runtime_component_id('gpu-acceleration'), 'semantic-core')

        runtime_dir = TEST_ROOT / 'runtime3'
        semantic_root = runtime_components_root(runtime_dir) / 'semantic-core-20260101010101000'
        semantic_root.mkdir(parents=True, exist_ok=True)

        roots = runtime_component_live_roots(runtime_dir, 'gpu-acceleration')
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0].resolve(), semantic_root.resolve())

