from __future__ import annotations

import json
import sys
from pathlib import Path


RUNTIME_SUPPORT_DIRNAME = 'runtime_support'
RUNTIME_MANIFESTS_DIRNAME = 'manifests'
RUNTIME_INSTALL_DRIVER_FILENAME = 'install_runtime_driver.py'
RUNTIME_BUNDLED_PYTHON_DIRNAME = 'python'
RUNTIME_BUNDLED_PYTHON_METADATA_FILENAME = 'bundled_python.json'


def application_root_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def runtime_support_dir(app_dir: str | Path | None = None) -> Path:
    root = Path(app_dir) if app_dir is not None else application_root_dir()
    return root / RUNTIME_SUPPORT_DIRNAME


def runtime_install_driver_path(app_dir: str | Path | None = None) -> Path:
    return runtime_support_dir(app_dir) / RUNTIME_INSTALL_DRIVER_FILENAME


def runtime_manifest_dir(app_dir: str | Path | None = None) -> Path:
    return runtime_support_dir(app_dir) / RUNTIME_MANIFESTS_DIRNAME


def runtime_manifest_path(
    profile: str,
    component: str,
    *,
    app_dir: str | Path | None = None,
) -> Path:
    normalized_profile = (profile or 'cpu').strip().lower() or 'cpu'
    normalized_component = (component or 'all').strip().lower() or 'all'
    return runtime_manifest_dir(app_dir) / normalized_profile / f'{normalized_component}.json'


def load_runtime_manifest(
    profile: str,
    component: str,
    *,
    app_dir: str | Path | None = None,
) -> dict[str, object]:
    manifest_path = runtime_manifest_path(profile, component, app_dir=app_dir)
    payload = json.loads(manifest_path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise RuntimeError(f'Invalid runtime manifest payload: {manifest_path}')
    payload.setdefault('manifest_path', str(manifest_path))
    return payload


def bundled_python_dir(app_dir: str | Path | None = None) -> Path:
    return runtime_support_dir(app_dir) / RUNTIME_BUNDLED_PYTHON_DIRNAME


def bundled_python_executable(app_dir: str | Path | None = None) -> Path:
    metadata = bundled_python_metadata(app_dir)
    relative = str(metadata.get('python_executable_relative') or 'python.exe').strip() or 'python.exe'
    return bundled_python_dir(app_dir) / relative


def bundled_python_metadata_path(app_dir: str | Path | None = None) -> Path:
    return runtime_support_dir(app_dir) / RUNTIME_BUNDLED_PYTHON_METADATA_FILENAME


def bundled_python_metadata(app_dir: str | Path | None = None) -> dict[str, object]:
    metadata_path = bundled_python_metadata_path(app_dir)
    if not metadata_path.exists():
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def bundled_python_ready(app_dir: str | Path | None = None) -> bool:
    python_exe = bundled_python_executable(app_dir)
    return python_exe.exists() and python_exe.is_file()


def runtime_install_log_dir(runtime_root: str | Path) -> Path:
    runtime_root = Path(runtime_root)
    shared_root = runtime_root.parent
    return shared_root / 'logs' / 'runtime'


def runtime_download_cache_dir(runtime_root: str | Path) -> Path:
    runtime_root = Path(runtime_root)
    return runtime_root / '_downloads'
