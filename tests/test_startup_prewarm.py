from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.extensions.service import ExtensionService
from omniclip_rag.startup_prewarm import (
    GIB,
    MAX_COMMIT_USAGE_PERCENT,
    MAX_STATUS_REFRESH_COMMIT_USAGE_PERCENT,
    MemoryStatus,
    evaluate_startup_prewarm,
    evaluate_startup_status_refresh,
    run_runtime_probe_worker,
    runtime_probe_command,
)
from omniclip_rag.vector_index import LanceDbVectorIndex, runtime_guidance_context


class StartupPrewarmTests(unittest.TestCase):
    def _memory(
        self,
        *,
        available_physical: int = 10 * GIB,
        commit_limit: int = 40 * GIB,
        commit_headroom: int = 12 * GIB,
    ) -> MemoryStatus:
        return MemoryStatus(
            total_physical_bytes=32 * GIB,
            available_physical_bytes=available_physical,
            commit_limit_bytes=commit_limit,
            commit_headroom_bytes=commit_headroom,
        )

    def test_gate_allows_only_complete_enabled_runtime_with_headroom(self) -> None:
        decision = evaluate_startup_prewarm(
            vector_backend='lancedb',
            runtime_complete=True,
            safe_mode=False,
            memory=self._memory(),
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, 'allowed')

    def test_gate_skips_disabled_safe_and_incomplete_modes(self) -> None:
        cases = [
            ({'vector_backend': 'disabled', 'runtime_complete': True, 'safe_mode': False}, 'backend-disabled'),
            ({'vector_backend': 'lancedb', 'runtime_complete': True, 'safe_mode': True}, 'safe-mode'),
            ({'vector_backend': 'lancedb', 'runtime_complete': False, 'safe_mode': False}, 'runtime-incomplete'),
        ]
        for values, reason in cases:
            with self.subTest(reason=reason):
                decision = evaluate_startup_prewarm(memory=self._memory(), **values)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason, reason)

    def test_gate_enforces_memory_boundaries(self) -> None:
        cases = [
            (self._memory(commit_headroom=8 * GIB - 1), 'low-commit-headroom'),
            (
                self._memory(
                    commit_limit=100 * GIB,
                    commit_headroom=int((100.0 - MAX_COMMIT_USAGE_PERCENT - 0.1) * GIB),
                ),
                'high-commit-usage',
            ),
            (self._memory(available_physical=6 * GIB - 1), 'low-physical-memory'),
        ]
        for memory, reason in cases:
            with self.subTest(reason=reason):
                decision = evaluate_startup_prewarm(
                    vector_backend='lancedb',
                    runtime_complete=True,
                    safe_mode=False,
                    memory=memory,
                )
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason, reason)

    def test_gate_accepts_exact_memory_boundaries(self) -> None:
        decision = evaluate_startup_prewarm(
            vector_backend='lancedb',
            runtime_complete=True,
            safe_mode=False,
            memory=self._memory(
                available_physical=6 * GIB,
                commit_limit=40 * GIB,
                commit_headroom=8 * GIB,
            ),
        )
        self.assertTrue(decision.allowed)

    def test_status_refresh_uses_lower_but_bounded_memory_gate(self) -> None:
        allowed = evaluate_startup_status_refresh(
            runtime_complete=True,
            safe_mode=False,
            memory=self._memory(
                available_physical=4 * GIB,
                commit_limit=100 * GIB,
                commit_headroom=3 * GIB,
            ),
        )
        self.assertTrue(allowed.allowed)
        safe_mode_allowed = evaluate_startup_status_refresh(
            runtime_complete=True,
            safe_mode=True,
            memory=self._memory(),
        )
        self.assertTrue(safe_mode_allowed.allowed)
        self.assertEqual(safe_mode_allowed.reason, 'allowed')

        cases = [
            (self._memory(commit_headroom=3 * GIB - 1), 'low-commit-headroom'),
            (
                self._memory(
                    commit_limit=200 * GIB,
                    commit_headroom=int((100.0 - MAX_STATUS_REFRESH_COMMIT_USAGE_PERCENT - 0.1) * 2 * GIB),
                ),
                'high-commit-usage',
            ),
            (self._memory(available_physical=4 * GIB - 1), 'low-physical-memory'),
        ]
        for memory, reason in cases:
            with self.subTest(reason=reason):
                decision = evaluate_startup_status_refresh(
                    runtime_complete=True,
                    safe_mode=False,
                    memory=memory,
                )
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason, reason)

    def test_source_probe_command_uses_launcher_without_shell(self) -> None:
        command = runtime_probe_command(
            probe_kind='refresh',
            output_path=Path('probe.json'),
            data_root='D:/data',
        )
        self.assertEqual(Path(command[0]), Path(os.sys.executable))
        self.assertTrue(command[1].endswith('launcher.py'))
        self.assertIn('--runtime-probe-worker', command)
        self.assertIn('--data-root', command)

    def test_workspace_status_worker_writes_json_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(prefix='omniclip-workspace-status-') as temp_dir:
            root = Path(temp_dir)
            paths = ensure_data_paths(str(root), '')
            config = AppConfig(vault_path='', data_root=str(paths.global_root), vector_backend='disabled')
            output = root / 'workspace-status.json'

            with patch.dict(os.environ, {}, clear=False), \
                 patch('omniclip_rag.config.build_data_paths', return_value=paths), \
                 patch('omniclip_rag.config.load_config', return_value=config), \
                 patch('omniclip_rag.service.OmniClipService') as service_type:
                service_type.return_value.status_snapshot.return_value = {'vector_backend': 'disabled'}
                exit_code = run_runtime_probe_worker(
                    probe_kind='workspace-status',
                    output_path=str(output),
                    data_root=str(paths.global_root),
                )

            payload = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload['status'], 'ok')
            self.assertEqual(payload['probe_kind'], 'workspace-status')
            self.assertEqual(payload['payload']['vector_backend'], 'disabled')
            service_type.return_value.close.assert_called_once()

    def test_worker_writes_success_payload_without_loading_models(self) -> None:
        output = Path(tempfile.mkdtemp(prefix='omniclip-prewarm-test-')) / 'result.json'

        class _ImportContext:
            def __enter__(self):
                return None

            def __exit__(self, exc_type, exc, tb):
                return False

        try:
            real_import = __import__

            def fake_import(name, *args, **kwargs):
                if name in {'pyarrow', 'lancedb'}:
                    return object()
                return real_import(name, *args, **kwargs)

            with patch('omniclip_rag.vector_index.detect_acceleration', return_value={'cuda_available': False}), \
                 patch('omniclip_rag.vector_index._runtime_import_environment', return_value=_ImportContext()), \
                 patch('builtins.__import__', side_effect=fake_import) as import_mock:
                result = run_runtime_probe_worker(
                    probe_kind='startup-prewarm',
                    output_path=str(output),
                )
            self.assertEqual(result, 0)
            payload = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(payload['status'], 'ok')
            imported = [str(call.args[0]) for call in import_mock.call_args_list if call.args]
            self.assertIn('pyarrow', imported)
            self.assertIn('lancedb', imported)
            self.assertFalse(any(name.startswith('sentence_transformers.models') for name in imported))
        finally:
            if output.exists():
                output.unlink()
            output.parent.rmdir()

    def test_lancedb_status_does_not_open_table_or_import_schema(self) -> None:
        root = Path(tempfile.mkdtemp(prefix='omniclip-status-test-'))
        try:
            paths = ensure_data_paths(str(root / 'data'), str(root / 'vault'))
            config = AppConfig(
                vault_path=str(root / 'vault'),
                data_root=str(paths.global_root),
                vector_backend='lancedb',
            )
            index = LanceDbVectorIndex(config, paths)
            with patch.object(index, '_table_exists', return_value=True), \
                 patch.object(index, '_table', side_effect=AssertionError('status must not open LanceDB')), \
                 patch('omniclip_rag.vector_index.inspect_runtime_environment', return_value={'runtime_complete': True}):
                status = index.status()
            self.assertTrue(status['table_ready'])
            self.assertEqual(status['vector_dimension'], 0)
        finally:
            import shutil

            shutil.rmtree(root, ignore_errors=True)

    def test_extension_source_summaries_disable_runtime_probe(self) -> None:
        service = ExtensionService()

        class _SummaryService:
            def source_summaries(self, *, source_paths=None):
                return {}

            def close(self):
                return None

        with patch.object(service, '_pdf_service', return_value=_SummaryService()) as pdf_factory, \
             patch.object(service, '_tika_service', return_value=_SummaryService()) as tika_factory:
            self.assertEqual(service.run_pdf_source_summaries(), {})
            self.assertEqual(service.run_tika_source_summaries(), {})
        pdf_factory.assert_called_once_with(probe_vector_runtime=False)
        tika_factory.assert_called_once_with(probe_vector_runtime=False)

    def test_runtime_guidance_reuses_supplied_acceleration_without_probe(self) -> None:
        runtime_root = Path(tempfile.mkdtemp(prefix='omniclip-guidance-test-'))
        try:
            acceleration = {
                'runtime_complete': True,
                'torch_available': True,
                'sentence_transformers_available': True,
                'cuda_available': False,
                'gpu_present': False,
            }
            runtime_state = {
                'runtime_dir': runtime_root,
                'active_runtime_dir': runtime_root,
                'preferred_runtime_dir': runtime_root,
                'runtime_exists': True,
                'runtime_has_content': True,
                'runtime_complete': True,
                'runtime_missing_items': [],
                'runtime_pending': False,
                'runtime_pending_components': [],
            }
            with patch('omniclip_rag.vector_index.detect_acceleration', side_effect=AssertionError('must reuse supplied payload')):
                context = runtime_guidance_context(
                    'torch',
                    'auto',
                    acceleration_payload=acceleration,
                    runtime_state=runtime_state,
                    runtime_root=runtime_root,
                )
            self.assertEqual(context['resolved_device'], 'cpu')
            self.assertTrue(context['runtime_complete'])
        finally:
            runtime_root.rmdir()


if __name__ == '__main__':
    unittest.main()
