from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

from .config import ensure_data_paths
from .query_subprocess import config_from_payload, write_json_atomic
from .service import OmniClipService


_WATCH_VECTOR_BATCH_SIZES = {
    5: 1,
    10: 2,
    15: 3,
    20: 4,
    30: 6,
    40: 8,
    50: 10,
    60: 12,
    70: 14,
    80: 16,
    90: 16,
}


def watch_reindex_worker_command(*, request_path: Path, output_path: Path) -> list[str]:
    args = [
        '--watch-reindex-worker',
        '--request',
        str(request_path),
        '--output',
        str(output_path),
    ]
    if getattr(sys, 'frozen', False):
        return [sys.executable, *args]
    launcher = Path(__file__).resolve().parents[1] / 'launcher.py'
    return [sys.executable, str(launcher), *args]


def watch_reindex_worker_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment['OMNICLIP_INTERNAL_WORKER'] = '1'
    environment['PYTHONUNBUFFERED'] = '1'
    return environment


def execute_watch_reindex_request(request: dict[str, object]) -> dict[str, object]:
    config = config_from_payload(dict(request.get('config') or {}))
    peak = max(min(int(getattr(config, 'watch_resource_peak_percent', 15) or 15), 90), 5)
    effective_batch_size = _WATCH_VECTOR_BATCH_SIZES.get(peak, 3)
    config.vector_batch_size = min(
        max(int(getattr(config, 'vector_batch_size', 16) or 16), 1),
        effective_batch_size,
    )
    paths = ensure_data_paths(config.data_root, config.vault_path)
    changed = [
        str(item).strip()
        for item in list(request.get('changed') or [])
        if str(item).strip()
    ]
    deleted = [
        str(item).strip()
        for item in list(request.get('deleted') or [])
        if str(item).strip()
    ]
    service = OmniClipService(config, paths)
    try:
        repair_events: list[dict[str, object]] = []
        watch_state = service._read_watch_state() or {}
        repair_needed = any(
            bool(watch_state.get(key))
            for key in ('dirty_paths', 'dirty_vector_paths', 'dirty_vector_chunk_ids')
        )
        if repair_needed:
            current_snapshot, _offline_reason = service._snapshot_safe()
            if current_snapshot is not None:
                repair_events = service._repair_watch_state(current_snapshot)
        stats = service.reindex_paths(changed, deleted) if changed or deleted else service.store.stats()
        return {
            'stats': stats,
            'events': repair_events,
            'changed': changed,
            'deleted': deleted,
            'effective_vector_batch_size': config.vector_batch_size,
        }
    finally:
        # This worker exits immediately. Let Windows tear down the Torch/CUDA
        # runtime instead of unloading native modules inside a live GUI process.
        service.close(release_process_resources=False)


def run_watch_reindex_worker(*, request_path: str, output_path: str) -> int:
    request_target = Path(request_path)
    output_target = Path(output_path)
    try:
        request = json.loads(request_target.read_text(encoding='utf-8'))
        payload = execute_watch_reindex_request(dict(request))
        write_json_atomic(output_target, {'status': 'ok', **payload})
        return 0
    except BaseException as exc:
        try:
            write_json_atomic(
                output_target,
                {
                    'status': 'error',
                    'error_class': exc.__class__.__name__,
                    'error_message': str(exc).strip() or exc.__class__.__name__,
                    'traceback': traceback.format_exc(),
                },
            )
        except OSError:
            pass
        return 1
