from __future__ import annotations

import json
import os
import tempfile
import time
import threading
from pathlib import Path

from ..config import DataPaths

EXTENSION_BUILD_STATE_VERSION = 1
EXTENSION_BUILD_STATE_FILE_NAME = 'build_state.json'
EXTENSION_BUILD_LEASE_FILE_NAME = 'build_lease.json'
EXTENSION_DIAGNOSTICS_DIR_NAME = 'diagnostics'
EXTENSION_LEASE_STALE_SECONDS = 6 * 60 * 60
EXTENSION_DIAGNOSTIC_KEEP_LIMIT = 20
_JSON_WRITE_LOCK = threading.Lock()


def utc_now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def build_state_file(paths: DataPaths) -> Path:
    return paths.state_dir / EXTENSION_BUILD_STATE_FILE_NAME


def build_lease_file(paths: DataPaths) -> Path:
    return paths.state_dir / EXTENSION_BUILD_LEASE_FILE_NAME


def diagnostics_dir(paths: DataPaths) -> Path:
    directory = paths.logs_dir / EXTENSION_DIAGNOSTICS_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def read_extension_build_state(paths: DataPaths) -> dict[str, object] | None:
    return _read_json(build_state_file(paths))


def write_extension_build_state(paths: DataPaths, payload: dict[str, object]) -> Path:
    normalized = dict(payload)
    normalized.setdefault('state_version', EXTENSION_BUILD_STATE_VERSION)
    normalized['updated_at'] = utc_now()
    target = build_state_file(paths)
    _write_json_atomic(target, normalized)
    return target


def clear_extension_build_state(paths: DataPaths) -> None:
    try:
        build_state_file(paths).unlink(missing_ok=True)
    except OSError:
        pass


def read_extension_build_lease(paths: DataPaths) -> dict[str, object] | None:
    return _read_json(build_lease_file(paths))


def acquire_extension_build_lease(
    paths: DataPaths,
    *,
    pipeline: str,
    build_id: str,
    workspace_id: str,
    data_root: str,
    owner_pid: int | None = None,
    stale_after_seconds: int = EXTENSION_LEASE_STALE_SECONDS,
) -> dict[str, object]:
    target = build_lease_file(paths)
    owner_pid = int(owner_pid or os.getpid())
    existing = _read_json(target)
    if existing:
        existing_pid = int(existing.get('pid') or 0)
        updated_at = str(existing.get('updated_at') or existing.get('started_at') or '').strip()
        if _lease_is_active(existing_pid, updated_at, stale_after_seconds):
            raise RuntimeError('extension_build_lease_active')
    payload = {
        'pipeline': str(pipeline or '').strip().lower(),
        'build_id': str(build_id or '').strip(),
        'workspace_id': str(workspace_id or '').strip(),
        'data_root': str(data_root or '').strip(),
        'pid': owner_pid,
        'started_at': utc_now(),
        'updated_at': utc_now(),
    }
    _write_json_atomic(target, payload)
    return payload


def touch_extension_build_lease(paths: DataPaths, *, build_id: str = '') -> None:
    target = build_lease_file(paths)
    payload = _read_json(target)
    if not payload:
        return
    if build_id and str(payload.get('build_id') or '').strip() != str(build_id or '').strip():
        return
    payload['updated_at'] = utc_now()
    try:
        _write_json_atomic(target, payload)
    except OSError:
        return


def release_extension_build_lease(paths: DataPaths, *, build_id: str = '') -> None:
    target = build_lease_file(paths)
    payload = _read_json(target)
    if payload and build_id and str(payload.get('build_id') or '').strip() != str(build_id or '').strip():
        return
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass


def write_extension_diagnostic_report(
    paths: DataPaths,
    *,
    prefix: str,
    payload: dict[str, object],
) -> Path:
    directory = diagnostics_dir(paths)
    timestamp = time.strftime('%Y%m%d-%H%M%S', time.localtime())
    target = directory / f'{prefix}-{timestamp}.json'
    _write_json_atomic(target, dict(payload))
    _trim_old_diagnostics(directory, prefix)
    return target


def file_fingerprint(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        'path': str(path),
        'size': int(stat.st_size or 0),
        'mtime': float(stat.st_mtime or 0.0),
    }


def fingerprint_matches(path: Path, fingerprint: dict[str, object]) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    return (
        int(fingerprint.get('size') or 0) == int(stat.st_size or 0)
        and float(fingerprint.get('mtime') or 0.0) == float(stat.st_mtime or 0.0)
    )


def is_process_alive(pid: int) -> bool:
    normalized = int(pid or 0)
    if normalized <= 0:
        return False
    try:
        os.kill(normalized, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_json(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _JSON_WRITE_LOCK:
        temp_name = None
        with tempfile.NamedTemporaryFile(
            'w',
            delete=False,
            dir=path.parent,
            prefix=f'{path.name}.',
            suffix='.tmp',
            encoding='utf-8',
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            temp_name = handle.name
        last_error: OSError | None = None
        for _ in range(10):
            try:
                os.replace(temp_name, path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.02)
        if last_error is not None:
            raise last_error


def _lease_is_active(pid: int, updated_at: str, stale_after_seconds: int) -> bool:
    if pid and is_process_alive(pid):
        return True
    if not updated_at:
        return False
    try:
        struct_time = time.strptime(updated_at, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        return False
    updated_epoch = time.mktime(struct_time)
    return (time.time() - updated_epoch) < max(int(stale_after_seconds or 0), 0)


def _trim_old_diagnostics(directory: Path, prefix: str) -> None:
    try:
        files = sorted(directory.glob(f'{prefix}-*.json'))
    except OSError:
        return
    overflow = max(len(files) - EXTENSION_DIAGNOSTIC_KEEP_LIMIT, 0)
    for stale in files[:overflow]:
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            continue
