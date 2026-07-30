from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.query_subprocess import config_to_payload, write_json_atomic
from omniclip_rag.service import OmniClipService
from omniclip_rag.watch_subprocess import (
    watch_reindex_worker_command,
    watch_reindex_worker_environment,
)


class WatchReindexSubprocessTests(unittest.TestCase):
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
