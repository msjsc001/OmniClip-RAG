from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from .config import DataPaths

_RECOVERY_FILE_NAME = 'runtime_recovery.json'
_MEMORY_INCIDENT_KINDS = {'vector_oom', 'memory_pressure', 'startup_failure'}


def _state_file(paths: DataPaths) -> Path:
    return paths.shared_root / _RECOVERY_FILE_NAME


def _utc_now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _read_state(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_state(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    with tempfile.NamedTemporaryFile('w', delete=False, dir=path.parent, prefix=f'{path.name}.', suffix='.tmp', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        temp_name = handle.name
    os.replace(temp_name, path)


def prepare_startup_recovery(paths: DataPaths) -> dict[str, object]:
    path = _state_file(paths)
    previous = _read_state(path) or {}
    incident_kind = str(previous.get('incident_kind') or '').strip().lower()
    dirty_exit = not bool(previous.get('clean_exit', False)) and bool(previous)
    safe_startup = dirty_exit or incident_kind in _MEMORY_INCIDENT_KINDS
    if safe_startup:
        os.environ['OMNICLIP_SAFE_STARTUP'] = '1'
        try:
            from .vector_index import release_process_vector_resources
            release_process_vector_resources(clear_cuda=True, reset_acceleration=True)
        except Exception:
            pass
    else:
        os.environ.pop('OMNICLIP_SAFE_STARTUP', None)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return {
        'safe_startup': safe_startup,
        'dirty_exit': dirty_exit,
        'incident_kind': incident_kind,
        'incident_detail': str(previous.get('incident_detail') or ''),
        'previous_state': previous,
    }


def mark_session_started(paths: DataPaths, *, version: str, safe_startup: bool = False) -> None:
    payload = {
        'pid': os.getpid(),
        'version': version,
        'started_at': _utc_now(),
        'updated_at': _utc_now(),
        'clean_exit': False,
        'safe_startup': bool(safe_startup),
        'incident_kind': '',
        'incident_detail': '',
        'phase': '',
    }
    _write_state(_state_file(paths), payload)


def mark_session_running(paths: DataPaths) -> None:
    path = _state_file(paths)
    payload = _read_state(path) or {}
    payload.update({'updated_at': _utc_now(), 'phase': 'running'})
    _write_state(path, payload)


def mark_session_clean_exit(paths: DataPaths) -> None:
    path = _state_file(paths)
    payload = _read_state(path) or {}
    payload.update({'updated_at': _utc_now(), 'clean_exit': True, 'phase': 'closed'})
    _write_state(path, payload)


def record_runtime_incident(paths: DataPaths, *, kind: str, detail: str = '', phase: str = '') -> None:
    path = _state_file(paths)
    payload = _read_state(path) or {
        'pid': os.getpid(),
        'started_at': _utc_now(),
        'clean_exit': False,
    }
    payload.update(
        {
            'updated_at': _utc_now(),
            'clean_exit': False,
            'incident_kind': str(kind or '').strip().lower(),
            'incident_detail': str(detail or '').strip(),
            'phase': str(phase or '').strip(),
        }
    )
    _write_state(path, payload)
