from __future__ import annotations

import atexit
import ctypes
import ctypes.wintypes
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock
from urllib.parse import quote

from ...config import DataPaths
from ..models import TikaRuntimeStatus


LOGGER = logging.getLogger(__name__)
TIKA_VERSION = '3.2.3'
DEFAULT_TIKA_PORT = 9998
TIKA_JAR_NAME = f'tika-server-standard-{TIKA_VERSION}.jar'
TIKA_DOWNLOAD_URL = f'https://archive.apache.org/dist/tika/{TIKA_VERSION}/{TIKA_JAR_NAME}'
JRE_DOWNLOAD_URL = 'https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jre/hotspot/normal/eclipse'
HEALTHCHECK_PATH = '/tika'
_PARSE_PATH = '/tika'
_HEALTHY_HTTP_CODES = {200, 204, 405, 415}


@dataclass(slots=True)
class TikaRuntimeLayout:
    """Filesystem layout for the isolated Tika runtime bundle."""

    root: Path
    jre_root: Path
    jar_path: Path
    download_dir: Path


def runtime_layout(paths: DataPaths) -> TikaRuntimeLayout:
    """Return the isolated Tika runtime layout under shared data."""

    root = paths.shared_root / 'extensions_runtime' / 'tika'
    return TikaRuntimeLayout(
        root=root,
        jre_root=root / 'jre',
        jar_path=root / TIKA_JAR_NAME,
        download_dir=root / 'downloads',
    )


def detect_tika_runtime(paths: DataPaths, *, port: int = DEFAULT_TIKA_PORT) -> TikaRuntimeStatus:
    """Inspect the isolated runtime directory and report readiness."""

    layout = runtime_layout(paths)
    java_path = _find_java_executable(layout.jre_root)
    jar_path = layout.jar_path if layout.jar_path.exists() else None
    status = TikaRuntimeStatus(
        installed=bool(java_path and jar_path),
        java_available=java_path is not None,
        jar_available=jar_path is not None,
        version=TIKA_VERSION if jar_path is not None else '',
        install_root=str(layout.root),
        java_path=str(java_path) if java_path else '',
        jar_path=str(jar_path) if jar_path else '',
        port=port,
    )
    if not status.java_available:
        status.last_error = 'java_missing'
    elif not status.jar_available:
        status.last_error = 'tika_jar_missing'
    return status


def install_tika_runtime(
    paths: DataPaths,
    *,
    progress_callback=None,
    tika_url: str = TIKA_DOWNLOAD_URL,
    jre_url: str = JRE_DOWNLOAD_URL,
    port: int = DEFAULT_TIKA_PORT,
) -> TikaRuntimeStatus:
    """Download and unpack the isolated Tika runtime bundle.

    This runs inside a background worker so it may perform blocking network and
    archive work without touching the main UI thread.
    """

    layout = runtime_layout(paths)
    layout.root.mkdir(parents=True, exist_ok=True)
    layout.download_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix='omniclip_tika_', dir=str(layout.download_dir)) as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        jar_target = temp_dir / TIKA_JAR_NAME
        jre_archive = temp_dir / 'jre.zip'
        _emit_progress(progress_callback, stage='prepare', detail='Preparing isolated Tika runtime directories.')
        _download_file(tika_url, jar_target, progress_callback=progress_callback, stage='download_jar')
        _download_file(jre_url, jre_archive, progress_callback=progress_callback, stage='download_jre')
        _emit_progress(progress_callback, stage='extract_jre', detail='Extracting bundled Java runtime.')
        extracted_root = _extract_jre_archive(jre_archive, temp_dir / 'jre_extracted')
        if layout.jre_root.exists():
            shutil.rmtree(layout.jre_root, ignore_errors=True)
        shutil.move(str(extracted_root), str(layout.jre_root))
        shutil.copy2(jar_target, layout.jar_path)

    status = detect_tika_runtime(paths, port=port)
    if not status.installed:
        raise RuntimeError('Tika runtime install finished, but the bundle is still incomplete.')
    return status


def build_manual_install_context(paths: DataPaths) -> dict[str, str]:
    """Return user-facing manual install metadata for later UI guidance."""

    layout = runtime_layout(paths)
    return {
        'install_root': str(layout.root),
        'jre_root': str(layout.jre_root),
        'jar_path': str(layout.jar_path),
        'tika_url': TIKA_DOWNLOAD_URL,
        'jre_url': JRE_DOWNLOAD_URL,
    }


def check_tika_health(port: int = DEFAULT_TIKA_PORT, timeout: float = 1.0) -> bool:
    """Return whether the local Tika sidecar responds on the expected port."""

    url = f'http://127.0.0.1:{port}{HEALTHCHECK_PATH}'
    request = urllib.request.Request(url=url, method='GET')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(getattr(response, 'status', 200) or 200) in _HEALTHY_HTTP_CODES
    except urllib.error.HTTPError as exc:
        return int(getattr(exc, 'code', 0) or 0) in _HEALTHY_HTTP_CODES
    except OSError:
        return False


def parse_file_with_tika(file_path: Path, *, port: int = DEFAULT_TIKA_PORT, timeout: float = 60.0) -> str:
    """Send one file to the local Tika sidecar and request XHTML output."""

    path = Path(file_path).resolve()
    url = f'http://127.0.0.1:{port}{_PARSE_PATH}'
    headers = {
        'Accept': 'application/xhtml+xml',
        'Content-Type': 'application/octet-stream',
        'Content-Disposition': f'attachment; filename="{quote(path.name)}"',
    }
    with path.open('rb') as handle:
        payload = handle.read()
    request = urllib.request.Request(url=url, data=payload, headers=headers, method='PUT')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset('utf-8')
            return response.read().decode(charset, errors='replace')
    except urllib.error.HTTPError as exc:
        body = ''
        try:
            body = exc.read().decode('utf-8', errors='replace')
        except Exception:
            body = ''
        raise RuntimeError(f'Tika parse failed for {path.name}: HTTP {exc.code} {body.strip()}'.strip()) from exc
    except OSError as exc:
        raise RuntimeError(f'Tika parse transport failed for {path.name}: {exc}') from exc


class TikaSidecarManager:
    """Own the Tika subprocess and guarantee clean lifecycle boundaries."""

    def __init__(self, *, port: int = DEFAULT_TIKA_PORT, health_timeout: float = 15.0, poll_interval: float = 0.25) -> None:
        self._port = port
        self._health_timeout = health_timeout
        self._poll_interval = poll_interval
        self._lock = Lock()
        self._process: subprocess.Popen[str] | None = None
        self._job_handle = None
        self._install_root = ''
        atexit.register(self.shutdown)

    def status(self, paths: DataPaths) -> TikaRuntimeStatus:
        """Return filesystem readiness plus live process state."""

        with self._lock:
            status = detect_tika_runtime(paths, port=self._port)
            process = self._process
            if process is None:
                healthy = check_tika_health(self._port, timeout=0.5)
                if healthy and status.installed:
                    return replace(status, running=True, healthy=True, pid=0, port=self._port)
                return status
            if process.poll() is not None:
                self._clear_process_locked()
                return status
            healthy = check_tika_health(self._port, timeout=0.5)
            return replace(status, running=True, healthy=healthy, pid=int(process.pid or 0), port=self._port)

    def ensure_started(self, paths: DataPaths) -> TikaRuntimeStatus:
        """Start the Tika sidecar and wait until its HTTP endpoint becomes healthy."""

        with self._lock:
            status = detect_tika_runtime(paths, port=self._port)
            if not status.installed:
                return status
            if self._process is not None and self._process.poll() is None:
                healthy = check_tika_health(self._port, timeout=0.5)
                return replace(status, running=True, healthy=healthy, pid=int(self._process.pid or 0), port=self._port)
            if check_tika_health(self._port, timeout=0.5):
                return replace(status, running=True, healthy=True, pid=0, port=self._port)
            self._clear_process_locked()
            command = [status.java_path, '-jar', status.jar_path, '--port', str(self._port)]
            creation_flags = 0
            startupinfo = None
            if os.name == 'nt':
                creation_flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            LOGGER.info('Starting isolated Tika sidecar: %s', command)
            process = subprocess.Popen(
                command,
                cwd=status.install_root or None,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                startupinfo=startupinfo,
                creationflags=creation_flags,
            )
            self._process = process
            self._install_root = status.install_root
            self._job_handle = _assign_process_kill_on_close_job(process.pid)
            deadline = time.monotonic() + self._health_timeout
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    self._clear_process_locked()
                    raise RuntimeError('Tika sidecar exited before the health check passed.')
                if check_tika_health(self._port, timeout=0.5):
                    LOGGER.info('Tika sidecar is healthy on port %s.', self._port)
                    return replace(status, running=True, healthy=True, pid=int(process.pid or 0), port=self._port)
                time.sleep(self._poll_interval)
            self._terminate_process_locked(process)
            self._clear_process_locked()
            raise RuntimeError('Tika sidecar did not become healthy before the startup timeout elapsed.')

    def stop(self) -> TikaRuntimeStatus:
        """Stop the managed Tika sidecar process if it is running."""

        with self._lock:
            process = self._process
            install_root = self._install_root
            if process is not None and process.poll() is None:
                self._terminate_process_locked(process)
            self._clear_process_locked()
            status = TikaRuntimeStatus(install_root=install_root, port=self._port)
            if install_root:
                status = replace(status, install_root=install_root)
            return status

    def shutdown(self) -> None:
        """Best-effort shutdown hook for app exit and crash paths."""

        try:
            self.stop()
        except Exception:
            LOGGER.exception('Failed to stop isolated Tika sidecar during shutdown.')

    def _terminate_process_locked(self, process: subprocess.Popen[str]) -> None:
        try:
            process.terminate()
            process.wait(timeout=4)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=2)
            except Exception:
                LOGGER.exception('Unable to kill the Tika sidecar cleanly.')

    def _clear_process_locked(self) -> None:
        self._process = None
        self._install_root = ''
        if self._job_handle is not None and os.name == 'nt':
            try:
                ctypes.windll.kernel32.CloseHandle(self._job_handle)
            except Exception:
                LOGGER.exception('Failed to close the Tika job object handle.')
        self._job_handle = None


def _emit_progress(callback, **payload) -> None:
    if callback is None:
        return
    callback(dict(payload))


def _download_file(url: str, destination: Path, *, progress_callback=None, stage: str = 'download') -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url=url, headers={'User-Agent': 'OmniClipRAG/1.0'})
    with urllib.request.urlopen(request, timeout=30) as response, destination.open('wb') as handle:
        total = int(response.headers.get('Content-Length', '0') or 0)
        downloaded = 0
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            handle.write(chunk)
            downloaded += len(chunk)
            _emit_progress(
                progress_callback,
                stage=stage,
                downloaded=downloaded,
                total=total,
                detail=f'Downloading {destination.name}',
            )


def _extract_jre_archive(archive_path: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(destination)
    java_candidates = [path.parent.parent for path in destination.rglob('java.exe')]
    if not java_candidates:
        java_candidates = [path.parent.parent for path in destination.rglob('java')]
    if not java_candidates:
        raise RuntimeError('The downloaded JRE archive does not contain a Java executable.')
    return min(java_candidates, key=lambda item: len(item.parts))


def _find_java_executable(jre_root: Path) -> Path | None:
    candidates = [
        jre_root / 'bin' / 'java.exe',
        jre_root / 'bin' / 'java',
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    if not jre_root.exists():
        return None
    for pattern in ('java.exe', 'java'):
        for candidate in jre_root.rglob(pattern):
            if candidate.name.lower().startswith('java') and candidate.parent.name == 'bin':
                return candidate
    return None


def _assign_process_kill_on_close_job(pid: int):
    if os.name != 'nt' or pid <= 0:
        return None
    kernel32 = ctypes.windll.kernel32
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ('PerProcessUserTimeLimit', ctypes.c_longlong),
            ('PerJobUserTimeLimit', ctypes.c_longlong),
            ('LimitFlags', ctypes.wintypes.DWORD),
            ('MinimumWorkingSetSize', ctypes.c_size_t),
            ('MaximumWorkingSetSize', ctypes.c_size_t),
            ('ActiveProcessLimit', ctypes.wintypes.DWORD),
            ('Affinity', ctypes.c_size_t),
            ('PriorityClass', ctypes.wintypes.DWORD),
            ('SchedulingClass', ctypes.wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ('ReadOperationCount', ctypes.c_ulonglong),
            ('WriteOperationCount', ctypes.c_ulonglong),
            ('OtherOperationCount', ctypes.c_ulonglong),
            ('ReadTransferCount', ctypes.c_ulonglong),
            ('WriteTransferCount', ctypes.c_ulonglong),
            ('OtherTransferCount', ctypes.c_ulonglong),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ('IoInfo', IO_COUNTERS),
            ('ProcessMemoryLimit', ctypes.c_size_t),
            ('JobMemoryLimit', ctypes.c_size_t),
            ('PeakProcessMemoryUsed', ctypes.c_size_t),
            ('PeakJobMemoryUsed', ctypes.c_size_t),
        ]

    JobObjectExtendedLimitInformation = 9
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_TERMINATE = 0x0001

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    result = kernel32.SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not result:
        kernel32.CloseHandle(job)
        return None
    process_handle = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
    if not process_handle:
        kernel32.CloseHandle(job)
        return None
    try:
        assigned = kernel32.AssignProcessToJobObject(job, process_handle)
    finally:
        kernel32.CloseHandle(process_handle)
    if not assigned:
        kernel32.CloseHandle(job)
        return None
    return job


__all__ = [
    'DEFAULT_TIKA_PORT',
    'HEALTHCHECK_PATH',
    'JRE_DOWNLOAD_URL',
    'TIKA_DOWNLOAD_URL',
    'TIKA_JAR_NAME',
    'TIKA_VERSION',
    'TikaRuntimeLayout',
    'TikaSidecarManager',
    'build_manual_install_context',
    'check_tika_health',
    'detect_tika_runtime',
    'install_tika_runtime',
    'parse_file_with_tika',
    'runtime_layout',
]
