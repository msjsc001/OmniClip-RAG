from __future__ import annotations

import ctypes
import os
import subprocess
import time

from .process_utils import run_hidden
from collections import deque
from dataclasses import asdict, dataclass
from typing import Literal

from .config import AppConfig

BuildResourceProfile = Literal['quiet', 'balanced', 'peak']


@dataclass(slots=True)
class ResourceSample:
    timestamp: float
    cpu_percent: float | None = None
    memory_percent: float | None = None
    gpu_percent: float | None = None
    gpu_memory_percent: float | None = None
    gpu_memory_used_mb: float | None = None
    gpu_memory_total_mb: float | None = None


@dataclass(slots=True)
class BuildTuningSnapshot:
    profile: BuildResourceProfile
    encode_batch_size: int
    write_batch_size: int
    action: str = 'steady'
    reason: str = 'stable'
    oom_events: int = 0
    sample: ResourceSample | None = None

    def to_progress_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            'build_profile': self.profile,
            'encode_batch_size': self.encode_batch_size,
            'write_batch_size': self.write_batch_size,
            'tuning_action': self.action,
            'tuning_reason': self.reason,
            'oom_events': self.oom_events,
        }
        if self.sample is not None:
            payload['resource_sample'] = asdict(self.sample)
        return payload


_PROFILE_TARGETS: dict[BuildResourceProfile, dict[str, float]] = {
    'quiet': {
        'cpu_low': 12.0,
        'cpu_high': 35.0,
        'memory_soft': 62.0,
        'gpu_low': 15.0,
        'gpu_high': 38.0,
        'gpu_memory_soft': 62.0,
    },
    'balanced': {
        'cpu_low': 28.0,
        'cpu_high': 60.0,
        'memory_soft': 76.0,
        'gpu_low': 34.0,
        'gpu_high': 68.0,
        'gpu_memory_soft': 80.0,
    },
    'peak': {
        'cpu_low': 55.0,
        'cpu_high': 92.0,
        'memory_soft': 88.0,
        'gpu_low': 62.0,
        'gpu_high': 95.0,
        'gpu_memory_soft': 92.0,
    },
}


class ResourceMonitor:
    def __init__(self, device: str, *, sample_interval_seconds: float = 1.0) -> None:
        self.device = (device or 'cpu').strip().lower() or 'cpu'
        self.sample_interval_seconds = max(float(sample_interval_seconds), 0.5)
        self._last_sample: ResourceSample | None = None
        self._last_cpu_snapshot: tuple[int, int] | None = None
        self._gpu_cache_until = 0.0
        self._gpu_cache: tuple[float | None, float | None, float | None, float | None] = (None, None, None, None)

    def sample(self, *, force: bool = False) -> ResourceSample:
        now = time.time()
        if not force and self._last_sample is not None and (now - self._last_sample.timestamp) < self.sample_interval_seconds:
            return self._last_sample

        cpu_percent = self._sample_cpu_percent()
        memory_percent = self._sample_memory_percent()
        gpu_percent = None
        gpu_memory_percent = None
        gpu_memory_used_mb = None
        gpu_memory_total_mb = None
        if self.device == 'cuda':
            gpu_percent, gpu_memory_percent, gpu_memory_used_mb, gpu_memory_total_mb = self._sample_gpu()
        sample = ResourceSample(
            timestamp=now,
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            gpu_percent=gpu_percent,
            gpu_memory_percent=gpu_memory_percent,
            gpu_memory_used_mb=gpu_memory_used_mb,
            gpu_memory_total_mb=gpu_memory_total_mb,
        )
        self._last_sample = sample
        return sample

    def _sample_cpu_percent(self) -> float | None:
        if os.name != 'nt':
            return None
        try:
            idle, total = _read_system_cpu_times_windows()
        except Exception:
            return None
        previous = self._last_cpu_snapshot
        self._last_cpu_snapshot = (idle, total)
        if previous is None:
            return None
        prev_idle, prev_total = previous
        idle_delta = max(idle - prev_idle, 0)
        total_delta = max(total - prev_total, 0)
        if total_delta <= 0:
            return None
        busy = max(total_delta - idle_delta, 0)
        return max(0.0, min((busy / total_delta) * 100.0, 100.0))

    def _sample_memory_percent(self) -> float | None:
        if os.name != 'nt':
            return None
        try:
            return _read_system_memory_percent_windows()
        except Exception:
            return None

    def _sample_gpu(self) -> tuple[float | None, float | None, float | None, float | None]:
        now = time.time()
        if now < self._gpu_cache_until:
            return self._gpu_cache
        try:
            result = run_hidden(
                [
                    'nvidia-smi',
                    '--query-gpu=utilization.gpu,memory.used,memory.total',
                    '--format=csv,noheader,nounits',
                ],
                capture_output=True,
                text=True,
                timeout=1,
                check=True,
            )
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            self._gpu_cache = (None, None, None, None)
            self._gpu_cache_until = now + self.sample_interval_seconds
            return self._gpu_cache
        line = next((value.strip() for value in result.stdout.splitlines() if value.strip()), '')
        parts = [part.strip() for part in line.split(',')]
        if len(parts) < 3:
            self._gpu_cache = (None, None, None, None)
        else:
            try:
                gpu_percent = float(parts[0])
                memory_used_mb = float(parts[1])
                memory_total_mb = float(parts[2])
            except ValueError:
                self._gpu_cache = (None, None, None, None)
            else:
                gpu_memory_percent = (memory_used_mb / memory_total_mb * 100.0) if memory_total_mb > 0 else None
                self._gpu_cache = (gpu_percent, gpu_memory_percent, memory_used_mb, memory_total_mb)
        self._gpu_cache_until = now + self.sample_interval_seconds
        return self._gpu_cache


class BuildPerformanceController:
    def __init__(self, config: AppConfig, resolved_device: str, *, monitor: ResourceMonitor | None = None) -> None:
        self.profile: BuildResourceProfile = _normalize_profile(getattr(config, 'build_resource_profile', 'balanced'))
        self.resolved_device = (resolved_device or 'cpu').strip().lower() or 'cpu'
        self.monitor = monitor or ResourceMonitor(self.resolved_device)
        self.targets = _PROFILE_TARGETS[self.profile]
        self.base_batch_size = max(int(getattr(config, 'vector_batch_size', 16) or 16), 1)
        self.current_encode_batch_size = self._initial_encode_batch()
        self.current_write_batch_size = self._initial_write_batch()
        self.min_encode_batch_size = 1 if self.resolved_device != 'cuda' else 4
        self.max_encode_batch_size = self._max_encode_batch()
        self.min_write_batch_size = 128
        self.max_write_batch_size = self._max_write_batch()
        self.oom_events = 0
        self._last_adjustment_at = 0.0
        self._cooldown_until = 0.0
        self._history: deque[tuple[float, float]] = deque(maxlen=4)

    def snapshot(self, sample: ResourceSample | None = None, *, action: str = 'steady', reason: str = 'stable') -> BuildTuningSnapshot:
        return BuildTuningSnapshot(
            profile=self.profile,
            encode_batch_size=self.current_encode_batch_size,
            write_batch_size=self.current_write_batch_size,
            action=action,
            reason=reason,
            oom_events=self.oom_events,
            sample=sample,
        )

    def note_oom(self) -> BuildTuningSnapshot:
        self.oom_events += 1
        self.current_encode_batch_size = max(self.min_encode_batch_size, self.current_encode_batch_size // 2)
        self.current_write_batch_size = max(self.min_write_batch_size, self.current_write_batch_size // 2)
        self._cooldown_until = time.time() + 8.0
        sample = self.monitor.sample(force=True)
        return self.snapshot(sample, action='shrink', reason='oom_recovery')

    def note_pressure(
        self,
        *,
        reason: str = 'memory_pressure',
        action: str = 'hold',
        shrink_ratio: float = 0.75,
        cooldown_seconds: float = 1.5,
        sample: ResourceSample | None = None,
        force_sample: bool = False,
    ) -> BuildTuningSnapshot:
        safe_ratio = max(min(float(shrink_ratio), 0.98), 0.35)
        next_encode = max(self.min_encode_batch_size, int(self.current_encode_batch_size * safe_ratio))
        next_write = max(self.min_write_batch_size, int(self.current_write_batch_size * safe_ratio))
        if next_encode >= self.current_encode_batch_size and self.current_encode_batch_size > self.min_encode_batch_size:
            next_encode = self.current_encode_batch_size - 1
        if next_write >= self.current_write_batch_size and self.current_write_batch_size > self.min_write_batch_size:
            next_write = max(self.min_write_batch_size, self.current_write_batch_size - 64)
        self.current_encode_batch_size = max(self.min_encode_batch_size, next_encode)
        self.current_write_batch_size = max(self.min_write_batch_size, next_write)
        self._cooldown_until = max(self._cooldown_until, time.time() + max(float(cooldown_seconds), 0.25))
        active_sample = sample if sample is not None else self.monitor.sample(force=force_sample)
        return self.snapshot(active_sample, action=action, reason=reason)

    def in_cooldown(self) -> bool:
        return time.time() < self._cooldown_until

    def observe(
        self,
        *,
        encode_elapsed_ms: float,
        write_elapsed_ms: float,
        prepare_elapsed_ms: float = 0.0,
        write_queue_depth: int | None = None,
        write_queue_capacity: int | None = None,
        progress_ratio: float | None = None,
    ) -> BuildTuningSnapshot:
        sample = self.monitor.sample(force=False)
        combined_write_elapsed_ms = max(write_elapsed_ms, 0.0) + max(prepare_elapsed_ms, 0.0)
        self._history.append((max(encode_elapsed_ms, 0.0), combined_write_elapsed_ms))
        now = sample.timestamp
        if now < self._cooldown_until or (now - self._last_adjustment_at) < self.monitor.sample_interval_seconds:
            return self.snapshot(sample)
        self._last_adjustment_at = now
        average_encode = sum(item[0] for item in self._history) / max(len(self._history), 1)
        average_write = sum(item[1] for item in self._history) / max(len(self._history), 1)

        cpu = sample.cpu_percent if sample.cpu_percent is not None else 0.0
        memory = sample.memory_percent if sample.memory_percent is not None else 0.0
        gpu = sample.gpu_percent if sample.gpu_percent is not None else None
        gpu_memory = sample.gpu_memory_percent if sample.gpu_memory_percent is not None else 0.0
        queue_fill = 0.0
        if write_queue_depth is not None and write_queue_capacity:
            queue_fill = max(0.0, min(float(write_queue_depth) / max(float(write_queue_capacity), 1.0), 1.0))
        late_tail = max(float(progress_ratio or 0.0), 0.0) >= 0.72

        if memory >= self.targets['memory_soft'] or gpu_memory >= self.targets['gpu_memory_soft']:
            self.current_encode_batch_size = max(self.min_encode_batch_size, int(self.current_encode_batch_size * 0.75))
            self.current_write_batch_size = max(self.min_write_batch_size, int(self.current_write_batch_size * 0.75))
            return self.snapshot(sample, action='shrink', reason='memory_pressure')

        if queue_fill >= 0.85 and average_write >= average_encode * (0.72 if late_tail else 0.8) and memory < self.targets['memory_soft'] * 0.95:
            write_step = max(self.base_batch_size * (12 if late_tail else 8), 128)
            cool_ratio = 0.9 if late_tail else 0.92
            self.current_write_batch_size = min(self.max_write_batch_size, self.current_write_batch_size + write_step)
            self.current_encode_batch_size = max(self.min_encode_batch_size, int(self.current_encode_batch_size * cool_ratio))
            return self.snapshot(sample, action='expand', reason='write_overhead')

        if self.resolved_device == 'cuda' and gpu is not None:
            if gpu < self.targets['gpu_low'] and cpu < self.targets['cpu_high'] and gpu_memory < self.targets['gpu_memory_soft'] * 0.92:
                self.current_encode_batch_size = min(self.max_encode_batch_size, max(self.current_encode_batch_size + max(self.base_batch_size // 2, 2), int(self.current_encode_batch_size * 1.25)))
                if average_write > average_encode * 1.4:
                    self.current_write_batch_size = min(self.max_write_batch_size, self.current_write_batch_size + max(self.base_batch_size * 8, 128))
                    return self.snapshot(sample, action='expand', reason='gpu_idle_write_heavy')
                return self.snapshot(sample, action='expand', reason='gpu_headroom')
            if gpu > self.targets['gpu_high']:
                self.current_encode_batch_size = max(self.min_encode_batch_size, int(self.current_encode_batch_size * 0.85))
                return self.snapshot(sample, action='shrink', reason='gpu_pressure')

        if average_write > average_encode * (1.18 if late_tail else 1.35) and cpu < self.targets['cpu_high'] and memory < self.targets['memory_soft'] * 0.95:
            self.current_write_batch_size = min(self.max_write_batch_size, self.current_write_batch_size + max(self.base_batch_size * (10 if late_tail else 8), 128))
            if late_tail:
                self.current_encode_batch_size = max(self.min_encode_batch_size, int(self.current_encode_batch_size * 0.94))
            return self.snapshot(sample, action='expand', reason='write_overhead')

        if cpu < self.targets['cpu_low'] and memory < self.targets['memory_soft'] * 0.9:
            self.current_encode_batch_size = min(self.max_encode_batch_size, self.current_encode_batch_size + max(self.base_batch_size // 2, 1))
            return self.snapshot(sample, action='expand', reason='cpu_headroom')

        if cpu > self.targets['cpu_high']:
            self.current_encode_batch_size = max(self.min_encode_batch_size, int(self.current_encode_batch_size * 0.85))
            if average_write >= average_encode:
                self.current_write_batch_size = max(self.min_write_batch_size, int(self.current_write_batch_size * 0.9))
            return self.snapshot(sample, action='shrink', reason='cpu_pressure')

        return self.snapshot(sample)

    def _initial_encode_batch(self) -> int:
        if self.resolved_device == 'cuda':
            if self.profile == 'quiet':
                return max(self.base_batch_size, 8)
            if self.profile == 'peak':
                return max(self.base_batch_size * 5, 64)
            return max(self.base_batch_size * 2, 24)
        if self.profile == 'quiet':
            return max(self.base_batch_size // 2, 4)
        if self.profile == 'peak':
            return max(self.base_batch_size * 2, 24)
        return max(self.base_batch_size, 12)

    def _initial_write_batch(self) -> int:
        if self.resolved_device == 'cuda':
            if self.profile == 'quiet':
                return 512
            if self.profile == 'peak':
                return 3072
            return 1024
        if self.profile == 'quiet':
            return 256
        if self.profile == 'peak':
            return 1024
        return 512

    def _max_encode_batch(self) -> int:
        if self.resolved_device == 'cuda':
            return 256 if self.profile == 'peak' else 128 if self.profile == 'balanced' else 64
        return 64 if self.profile == 'peak' else 48 if self.profile == 'balanced' else 24

    def _max_write_batch(self) -> int:
        if self.resolved_device == 'cuda':
            return 6144 if self.profile == 'peak' else 2048 if self.profile == 'balanced' else 1024
        return 2048 if self.profile == 'peak' else 1024 if self.profile == 'balanced' else 512


def normalize_build_resource_profile(value: str | None) -> BuildResourceProfile:
    return _normalize_profile(value)


def format_resource_sample(sample: ResourceSample | None) -> str:
    if sample is None:
        return ''
    parts: list[str] = []
    if sample.cpu_percent is not None:
        parts.append(f'CPU {sample.cpu_percent:.0f}%')
    if sample.memory_percent is not None:
        parts.append(f'RAM {sample.memory_percent:.0f}%')
    if sample.gpu_percent is not None:
        parts.append(f'GPU {sample.gpu_percent:.0f}%')
    if sample.gpu_memory_percent is not None:
        parts.append(f'显存 {sample.gpu_memory_percent:.0f}%')
    return ' / '.join(parts)


def _normalize_profile(value: str | None) -> BuildResourceProfile:
    normalized = str(value or 'balanced').strip().lower()
    if normalized in {'quiet', 'balanced', 'peak'}:
        return normalized  # type: ignore[return-value]
    return 'balanced'


def _read_system_cpu_times_windows() -> tuple[int, int]:
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [('dwLowDateTime', wintypes.DWORD), ('dwHighDateTime', wintypes.DWORD)]

    idle = FILETIME()
    kernel = FILETIME()
    user = FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        raise OSError('GetSystemTimes failed')

    def _value(ft: FILETIME) -> int:
        return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)

    idle_value = _value(idle)
    kernel_value = _value(kernel)
    user_value = _value(user)
    total_value = kernel_value + user_value
    return idle_value, total_value


def _read_system_memory_percent_windows() -> float:
    from ctypes import wintypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ('dwLength', wintypes.DWORD),
            ('dwMemoryLoad', wintypes.DWORD),
            ('ullTotalPhys', ctypes.c_ulonglong),
            ('ullAvailPhys', ctypes.c_ulonglong),
            ('ullTotalPageFile', ctypes.c_ulonglong),
            ('ullAvailPageFile', ctypes.c_ulonglong),
            ('ullTotalVirtual', ctypes.c_ulonglong),
            ('ullAvailVirtual', ctypes.c_ulonglong),
            ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
        ]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError('GlobalMemoryStatusEx failed')
    return float(status.dwMemoryLoad)

