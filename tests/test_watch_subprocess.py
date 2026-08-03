from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.query_subprocess import config_to_payload, write_json_atomic
from omniclip_rag.service import OmniClipService
from omniclip_rag.watch_subprocess import (
    execute_watch_reindex_request,
    watch_reindex_worker_command,
    watch_reindex_worker_environment,
)


class WatchReindexSubprocessTests(unittest.TestCase):
    def test_failed_historical_repair_does_not_starve_new_markdown_change(self) -> None:
        root = Path(tempfile.mkdtemp(prefix='omniclip-watch-repair-priority-test-'))
        try:
            vault = root / 'vault'
            vault.mkdir()
            target = vault / 'page.md'
            target.write_text('- old text\n', encoding='utf-8')
            paths = ensure_data_paths(str(root / 'data'), str(vault))
            config = AppConfig(
                vault_path=str(vault),
                data_root=str(paths.global_root),
                vector_backend='disabled',
                reranker_enabled=False,
            )
            service = OmniClipService(config, paths)
            try:
                service.rebuild_index()
                service._update_watch_state(add_vector_chunk_ids=['stale-vector'])
            finally:
                service.close()

            target.write_text('- newest text still indexed\n', encoding='utf-8')
            with patch.object(OmniClipService, '_repair_watch_state', side_effect=RuntimeError('repair boom')):
                payload = execute_watch_reindex_request(
                    {
                        'config': config_to_payload(config),
                        'changed': ['page.md'],
                        'deleted': [],
                    }
                )

            self.assertEqual(payload['changed'], ['page.md'])
            self.assertEqual(payload['events'][0]['kind'], 'repair_retry')
            service = OmniClipService(config, paths)
            try:
                self.assertTrue(service.store.search_candidates('newest', 5))
                self.assertIn('stale-vector', (service._read_watch_state() or {}).get('dirty_vector_chunk_ids', []))
            finally:
                service.close()
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_worker_repairs_only_one_bounded_history_slice_per_run(self) -> None:
        root = Path(tempfile.mkdtemp(prefix='omniclip-watch-repair-bounded-test-'))
        try:
            vault = root / 'vault'
            vault.mkdir()
            (vault / 'page.md').write_text('- indexed text\n', encoding='utf-8')
            paths = ensure_data_paths(str(root / 'data'), str(vault))
            config = AppConfig(
                vault_path=str(vault),
                data_root=str(paths.global_root),
                vector_backend='disabled',
                reranker_enabled=False,
                watch_resource_peak_percent=5,
            )
            dirty_ids = [f'stale-{index}' for index in range(100)]
            service = OmniClipService(config, paths)
            try:
                service.rebuild_index()
                service._update_watch_state(add_vector_chunk_ids=dirty_ids)
            finally:
                service.close()

            payload = execute_watch_reindex_request(
                {
                    'config': config_to_payload(config),
                    'changed': [],
                    'deleted': [],
                }
            )
            self.assertEqual(payload['events'][0]['kind'], 'repair_progress')
            self.assertEqual(payload['events'][0]['vector_chunk_ids'], 32)
            self.assertEqual(payload['events'][0]['pending_vector_chunk_ids'], 68)
            service = OmniClipService(config, paths)
            try:
                self.assertEqual(len((service._read_watch_state() or {}).get('dirty_vector_chunk_ids', [])), 68)
            finally:
                service.close()
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_worker_updates_one_changed_markdown_file_and_exits(self) -> None:
        root = Path(tempfile.mkdtemp(prefix='omniclip-watch-worker-test-'))
        try:
            vault = root / 'vault'
            vault.mkdir()
            target = vault / 'page.md'
            target.write_text('- old text\n', encoding='utf-8')
            paths = ensure_data_paths(str(root / 'data'), str(vault))
            config = AppConfig(
                vault_path=str(vault),
                data_root=str(paths.global_root),
                vector_backend='disabled',
                reranker_enabled=False,
            )
            service = OmniClipService(config, paths)
            try:
                service.rebuild_index()
            finally:
                service.close()

            target.write_text('- changed text\n', encoding='utf-8')
            request_path = root / 'request.json'
            output_path = root / 'result.json'
            write_json_atomic(
                request_path,
                {
                    'config': config_to_payload(config),
                    'changed': ['page.md'],
                    'deleted': [],
                },
            )
            completed = subprocess.run(
                watch_reindex_worker_command(
                    request_path=request_path,
                    output_path=output_path,
                ),
                env=watch_reindex_worker_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0)
            payload = json.loads(output_path.read_text(encoding='utf-8'))
            self.assertEqual(payload['status'], 'ok')
            self.assertEqual(payload['changed'], ['page.md'])
            self.assertEqual(payload['effective_vector_batch_size'], 3)
            self.assertEqual(int((payload['stats'] or {}).get('files', 0)), 1)

            service = OmniClipService(config, paths)
            try:
                rows = service.store.search_candidates('changed', 5)
                self.assertTrue(rows)
            finally:
                service.close()
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
