from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path


GIB = 1024**3
STARTUP_IDLE_DELAY_MS = 5_000
STARTUP_PREWARM_TIMEOUT_SECONDS = 120
MIN_COMMIT_HEADROOM_BYTES = 8 * GIB
MAX_COMMIT_USAGE_PERCENT = 85.0
MIN_PHYSICAL_AVAILABLE_BYTES = 6 * GIB
MIN_STATUS_REFRESH_COMMIT_HEADROOM_BYTES = 3 * GIB
MAX_STATUS_REFRESH_COMMIT_USAGE_PERCENT = 97.0
MIN_STATUS_REFRESH_PHYSICAL_AVAILABLE_BYTES = 4 * GIB


@dataclass(frozen=True, slots=True)
class MemoryStatus:
    total_physical_bytes: int
    available_physical_bytes: int
    commit_limit_bytes: int
    commit_headroom_bytes: int

    @property
    def commit_usage_percent(self) -> float:
        if self.commit_limit_bytes <= 0:
            return 100.0
        committed = max(self.commit_limit_bytes - self.commit_headroom_bytes, 0)
        return committed * 100.0 / self.commit_limit_bytes


@dataclass(frozen=True, slots=True)
class PrewarmDecision:
    allowed: bool
    reason: str
    memory: MemoryStatus | None = None


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ('dwLength', ctypes.c_ulong),
        ('dwMemoryLoad', ctypes.c_ulong),
        ('ullTotalPhys', ctypes.c_ulonglong),
        ('ullAvailPhys', ctypes.c_ulonglong),
        ('ullTotalPageFile', ctypes.c_ulonglong),
        ('ullAvailPageFile', ctypes.c_ulonglong),
        ('ullTotalVirtual', ctypes.c_ulonglong),
        ('ullAvailVirtual', ctypes.c_ulonglong),
        ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
    ]


def read_memory_status() -> MemoryStatus | None:
    """Read Windows commit and physical-memory headroom without third-party imports."""
    if os.name != 'nt':
        return None
    state = _MemoryStatusEx()
    state.dwLength = ctypes.sizeof(_MemoryStatusEx)
    try:
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state))
    except (AttributeError, OSError):
        return None
    if not ok:
        return None
    return MemoryStatus(
        total_physical_bytes=int(state.ullTotalPhys),
        available_physical_bytes=int(state.ullAvailPhys),
        commit_limit_bytes=int(state.ullTotalPageFile),
        commit_headroom_bytes=int(state.ullAvailPageFile),
    )


def evaluate_startup_prewarm(
    *,
    vector_backend: str,
    runtime_complete: bool,
    safe_mode: bool,
    memory: MemoryStatus | None,
) -> PrewarmDecision:
    backend = str(vector_backend or '').strip().lower()
    if safe_mode:
        return PrewarmDecision(False, 'safe-mode', memory)
    if backend in {'', 'disabled', 'none', 'off'}:
        return PrewarmDecision(False, 'backend-disabled', memory)
    if not runtime_complete:
        return PrewarmDecision(False, 'runtime-incomplete', memory)
    if memory is None:
        return PrewarmDecision(False, 'memory-unavailable', None)
    if memory.commit_headroom_bytes < MIN_COMMIT_HEADROOM_BYTES:
        return PrewarmDecision(False, 'low-commit-headroom', memory)
    if memory.commit_usage_percent > MAX_COMMIT_USAGE_PERCENT:
        return PrewarmDecision(False, 'high-commit-usage', memory)
    if memory.available_physical_bytes < MIN_PHYSICAL_AVAILABLE_BYTES:
        return PrewarmDecision(False, 'low-physical-memory', memory)
    return PrewarmDecision(True, 'allowed', memory)


def evaluate_startup_status_refresh(
    *,
    runtime_complete: bool,
    safe_mode: bool,
    memory: MemoryStatus | None,
) -> PrewarmDecision:
    """Allow a status-only child probe under a lower, still-safe memory gate."""
    if safe_mode:
        return PrewarmDecision(False, 'safe-mode', memory)
    if not runtime_complete:
        return PrewarmDecision(False, 'runtime-incomplete', memory)
    if memory is None:
        return PrewarmDecision(False, 'memory-unavailable', None)
    if memory.commit_headroom_bytes < MIN_STATUS_REFRESH_COMMIT_HEADROOM_BYTES:
        return PrewarmDecision(False, 'low-commit-headroom', memory)
    if memory.commit_usage_percent > MAX_STATUS_REFRESH_COMMIT_USAGE_PERCENT:
        return PrewarmDecision(False, 'high-commit-usage', memory)
    if memory.available_physical_bytes < MIN_STATUS_REFRESH_PHYSICAL_AVAILABLE_BYTES:
        return PrewarmDecision(False, 'low-physical-memory', memory)
    return PrewarmDecision(True, 'allowed', memory)


def runtime_probe_command(*, probe_kind: str, output_path: Path, data_root: str = '') -> list[str]:
    args = [
        '--runtime-probe-worker',
        '--probe-kind',
        str(probe_kind),
        '--output',
        str(output_path),
    ]
    if str(data_root or '').strip():
        args.extend(['--data-root', str(data_root)])
    if getattr(sys, 'frozen', False):
        return [sys.executable, *args]
    launcher = Path(__file__).resolve().parents[1] / 'launcher.py'
    return [sys.executable, str(launcher), *args]


def runtime_probe_environment(*, data_root: str = '') -> dict[str, str]:
    environment = dict(os.environ)
    normalized_root = str(data_root or '').strip()
    if normalized_root:
        environment['OMNICLIP_DATA_ROOT'] = normalized_root
    environment['OMNICLIP_INTERNAL_WORKER'] = '1'
    return environment


def idle_priority_creationflags() -> int:
    if os.name != 'nt':
        return 0
    return (
        getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
        | getattr(subprocess, 'IDLE_PRIORITY_CLASS', 0x00000040)
    )


def run_runtime_probe_worker(*, probe_kind: str, output_path: str, data_root: str = '') -> int:
    """Run expensive runtime imports in a disposable process and return JSON."""
    target = Path(output_path).expanduser()
    normalized_kind = str(probe_kind or '').strip().lower()
    if str(data_root or '').strip():
        os.environ['OMNICLIP_DATA_ROOT'] = str(data_root).strip()
    payload: dict[str, object]
    exit_code = 0
    try:
        if normalized_kind == 'workspace-status':
            from .config import build_data_paths, load_config
            from .service import OmniClipService

            root = Path(str(data_root or '')).expanduser().resolve()
            config = load_config(build_data_paths(root))
            if config is None:
                raise RuntimeError(f'No OmniClip config found under {root}')
            paths = build_data_paths(root, vault_path=config.vault_path)
            service = OmniClipService(config, paths)
            try:
                snapshot = service.status_snapshot()
            finally:
                service.close()
            payload = {
                'status': 'ok',
                'probe_kind': normalized_kind,
                'payload': snapshot,
            }
        else:
            from .vector_index import (
                _runtime_import_environment,
                detect_acceleration,
                runtime_management_snapshot,
            )

            if normalized_kind == 'startup-prewarm':
                acceleration = detect_acceleration(force_refresh=True)
                with _runtime_import_environment(component_id='vector-store'):
                    __import__('pyarrow')
                    __import__('lancedb')
                payload = {
                    'status': 'ok',
                    'probe_kind': normalized_kind,
                    'payload': acceleration,
                }
            elif normalized_kind in {'refresh', 'verify'}:
                snapshot = runtime_management_snapshot(
                    force_refresh=True,
                    verify_gpu=normalized_kind == 'verify',
                )
                payload = {
                    'status': 'ok',
                    'probe_kind': normalized_kind,
                    'payload': snapshot,
                }
            else:
                raise ValueError(f'Unsupported runtime probe kind: {probe_kind}')
    except BaseException as exc:
        exit_code = 1
        payload = {
            'status': 'error',
            'probe_kind': normalized_kind,
            'error_class': exc.__class__.__name__,
            'error_message': str(exc).strip() or exc.__class__.__name__,
            'traceback': traceback.format_exc(),
        }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError:
        return 2
    return exit_code


def decision_log_payload(decision: PrewarmDecision) -> dict[str, object]:
    payload: dict[str, object] = {'allowed': decision.allowed, 'reason': decision.reason}
    if decision.memory is not None:
        payload['memory'] = {
            **asdict(decision.memory),
            'commit_usage_percent': round(decision.memory.commit_usage_percent, 2),
        }
    return payload
