from __future__ import annotations

import json
import shutil
from pathlib import Path


RUNTIME_PENDING_DIRNAME = '.pending'
RUNTIME_COMPONENTS_DIRNAME = 'components'
RUNTIME_COMPONENT_REGISTRY_FILENAME = '_runtime_components.json'
_RUNTIME_COMPONENT_ALIASES = {
    'compute-core': 'semantic-core',
    'model-stack': 'semantic-core',
    # GPU acceleration is a capability that rides on the semantic runtime stack.
    'gpu-acceleration': 'semantic-core',
}
_RUNTIME_RESERVED_NAMES = {RUNTIME_PENDING_DIRNAME, RUNTIME_COMPONENTS_DIRNAME, RUNTIME_COMPONENT_REGISTRY_FILENAME}
_COMPONENTIZED_RUNTIME_CLEANUP_PATTERNS = {
    'semantic-core': (
        'torch', 'torch-*dist-info',
        'functorch', 'functorch-*dist-info',
        'torchgen', 'torchgen-*dist-info',
        'numpy', 'numpy-*dist-info', 'numpy.libs',
        'scipy', 'scipy-*dist-info', 'scipy.libs',
        'sentence_transformers', 'sentence_transformers-*dist-info',
        'transformers', 'transformers-*dist-info',
        'huggingface_hub', 'huggingface_hub-*dist-info',
        'safetensors', 'safetensors-*dist-info',
    ),
    'vector-store': (
        'lancedb', 'lancedb-*dist-info',
        'onnxruntime', 'onnxruntime-*dist-info',
        'pyarrow', 'pyarrow-*dist-info', 'pyarrow.libs',
        'pandas', 'pandas-*dist-info',
    ),
}


def _remove_runtime_entry(target: Path) -> None:
    if not target.exists():
        return
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=False)
    else:
        target.unlink(missing_ok=True)


def normalize_runtime_component_id(component_id: str) -> str:
    normalized = str(component_id or '').strip().lower() or 'all'
    return _RUNTIME_COMPONENT_ALIASES.get(normalized, normalized)


def runtime_pending_root(runtime_dir: Path) -> Path:
    return Path(runtime_dir) / RUNTIME_PENDING_DIRNAME


def runtime_pending_component_root(runtime_dir: Path, component_id: str) -> Path:
    return runtime_pending_root(runtime_dir) / str(component_id)


def runtime_pending_component_payload_dir(runtime_dir: Path, component_id: str) -> Path:
    return runtime_pending_component_root(runtime_dir, component_id) / 'payload'


def runtime_pending_component_manifest_path(runtime_dir: Path, component_id: str) -> Path:
    return runtime_pending_component_root(runtime_dir, component_id) / 'manifest.json'


def runtime_components_root(runtime_dir: Path) -> Path:
    return Path(runtime_dir) / RUNTIME_COMPONENTS_DIRNAME


def runtime_component_registry_path(runtime_dir: Path) -> Path:
    return Path(runtime_dir) / RUNTIME_COMPONENT_REGISTRY_FILENAME


def load_runtime_component_registry(runtime_dir: Path) -> dict[str, dict[str, object]]:
    registry_path = runtime_component_registry_path(runtime_dir)
    if not registry_path.exists():
        return {}
    try:
        payload = json.loads(registry_path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, dict[str, object]] = {}
    for raw_key, raw_value in payload.items():
        component_name = normalize_runtime_component_id(str(raw_key or ''))
        if not component_name or not isinstance(raw_value, dict):
            continue
        normalized[component_name] = dict(raw_value)
    return normalized


def runtime_component_registered_root(runtime_dir: Path, component_id: str) -> Path | None:
    runtime_dir = Path(runtime_dir)
    component_name = normalize_runtime_component_id(component_id)
    registry = load_runtime_component_registry(runtime_dir)
    payload = registry.get(component_name)
    if not payload:
        return None
    raw_path = str(payload.get('path') or '').strip()
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = runtime_dir / raw_path
    candidate = candidate.resolve()
    if candidate.exists() and candidate.is_dir():
        return candidate
    # Portability salvage: older registries stored absolute paths. If the user
    # moves the onedir folder, the payload may still exist under the new runtime
    # root with the same directory name.
    if Path(raw_path).is_absolute():
        relocated = runtime_components_root(runtime_dir) / Path(raw_path).name
        try:
            relocated = relocated.resolve()
        except OSError:
            relocated = runtime_components_root(runtime_dir) / Path(raw_path).name
        if relocated.exists() and relocated.is_dir():
            return relocated
    return None


def _discover_runtime_component_root(runtime_dir: Path, component_name: str) -> Path | None:
    """Best-effort component discovery for legacy componentized layouts.

    Historically InstallRuntime.ps1 produced versioned component roots like
    `components/semantic-core-YYYYMMDD...` and registered absolute paths. When
    the registry becomes stale (e.g. after moving the dist folder), we still
    want to locate the best available payload.
    """

    runtime_dir = Path(runtime_dir)
    components_root = runtime_components_root(runtime_dir)
    if not components_root.exists() or not components_root.is_dir():
        return None

    normalized = normalize_runtime_component_id(component_name)
    canonical = components_root / normalized
    if canonical.exists() and canonical.is_dir():
        return canonical.resolve()

    prefix = f'{normalized}-'
    candidates: list[tuple[int, int, str, Path]] = []
    try:
        entries = list(components_root.iterdir())
    except OSError:
        return None

    for entry in entries:
        if not entry.is_dir():
            continue
        name = entry.name.lower()
        if not name.startswith(prefix):
            continue
        tail = name[len(prefix) :]
        version_int = int(tail) if tail.isdigit() else -1
        try:
            stat = entry.stat()
            mtime_ns = int(getattr(stat, 'st_mtime_ns', int(stat.st_mtime * 1_000_000_000)))
        except OSError:
            mtime_ns = 0
        candidates.append((version_int, mtime_ns, entry.name, entry))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    best = candidates[0][3]
    try:
        return best.resolve()
    except OSError:
        return best


def _has_componentized_runtime_layout(runtime_dir: Path) -> bool:
    runtime_dir = Path(runtime_dir)
    registry = load_runtime_component_registry(runtime_dir)
    if registry:
        return True
    components_root = runtime_components_root(runtime_dir)
    if not components_root.exists() or not components_root.is_dir():
        return False
    try:
        return any(entry.is_dir() for entry in components_root.iterdir())
    except OSError:
        return False


def _clear_legacy_runtime_entries(runtime_dir: Path, cleanup_patterns: list[str] | tuple[str, ...]) -> None:
    runtime_dir = Path(runtime_dir)
    for raw_name in cleanup_patterns:
        name = str(raw_name or '').strip()
        if not name or name in _RUNTIME_RESERVED_NAMES:
            continue
        if any(token in name for token in ('*', '?', '[')):
            for candidate in list(runtime_dir.glob(name)):
                if candidate.name in _RUNTIME_RESERVED_NAMES:
                    continue
                _remove_runtime_entry(candidate)
            continue
        _remove_runtime_entry(runtime_dir / name)


def runtime_component_live_root(runtime_dir: Path, component_id: str) -> Path:
    runtime_dir = Path(runtime_dir)
    normalized_component = normalize_runtime_component_id(component_id)
    registered = runtime_component_registered_root(runtime_dir, normalized_component)
    if registered is not None:
        return registered
    discovered = _discover_runtime_component_root(runtime_dir, normalized_component)
    if discovered is not None:
        return discovered
    return runtime_components_root(runtime_dir) / normalized_component


def runtime_component_live_roots(runtime_dir: Path, component_id: str) -> tuple[Path, ...]:
    runtime_dir = Path(runtime_dir)
    live_root = runtime_component_live_root(runtime_dir, component_id)
    if live_root.exists() and live_root.is_dir():
        return (live_root,)
    if _has_componentized_runtime_layout(runtime_dir):
        return ()
    if runtime_dir.exists() and runtime_dir.is_dir():
        return (runtime_dir,)
    return ()


def list_pending_runtime_updates(runtime_dir: Path) -> list[dict[str, object]]:
    pending_root = runtime_pending_root(runtime_dir)
    if not pending_root.exists() or not pending_root.is_dir():
        return []
    payloads: list[dict[str, object]] = []
    for manifest_path in sorted(pending_root.glob('*/manifest.json')):
        try:
            payload = json.loads(manifest_path.read_text(encoding='utf-8'))
        except Exception:
            continue
        payload['component'] = normalize_runtime_component_id(str(payload.get('component') or manifest_path.parent.name))
        payload['manifest_path'] = str(manifest_path)
        payloads.append(payload)
    return payloads


def _merge_runtime_tree(source_root: Path, target_root: Path) -> None:
    target_root.mkdir(parents=True, exist_ok=True)
    for entry in list(source_root.iterdir()):
        if entry.name in _RUNTIME_RESERVED_NAMES:
            continue
        target = target_root / entry.name
        _remove_runtime_entry(target)
        shutil.move(str(entry), str(target))


def _normalize_legacy_component_roots(runtime_dir: Path) -> list[str]:
    runtime_dir = Path(runtime_dir)
    components_root = runtime_components_root(runtime_dir)
    if not components_root.exists() or not components_root.is_dir():
        return []
    component_dirs = [entry for entry in components_root.iterdir() if entry.is_dir()]
    if not component_dirs:
        return []

    normalized: list[str] = []
    for component_dir in sorted(component_dirs, key=lambda path: path.name):
        component_name = normalize_runtime_component_id(component_dir.name)
        target_root = components_root / component_name
        if component_dir.resolve() == target_root.resolve():
            normalized.append(component_name)
            continue
        if target_root.exists():
            _merge_runtime_tree(component_dir, target_root)
            shutil.rmtree(component_dir, ignore_errors=True)
        else:
            shutil.move(str(component_dir), str(target_root))
        normalized.append(component_name)
    return list(dict.fromkeys(normalized))


def _sanitize_componentized_runtime_root(runtime_dir: Path) -> list[str]:
    runtime_dir = Path(runtime_dir)
    components_root = runtime_components_root(runtime_dir)
    if not components_root.exists() or not components_root.is_dir():
        return []
    cleaned: list[str] = []
    for component_name, cleanup_patterns in _COMPONENTIZED_RUNTIME_CLEANUP_PATTERNS.items():
        component_root = components_root / component_name
        if not component_root.exists() or not component_root.is_dir():
            continue
        _clear_legacy_runtime_entries(runtime_dir, cleanup_patterns)
        cleaned.append(component_name)
    return cleaned


def apply_pending_runtime_updates(runtime_dir: Path) -> list[str]:
    runtime_dir = Path(runtime_dir)
    normalized_components = _normalize_legacy_component_roots(runtime_dir)
    sanitized_components = _sanitize_componentized_runtime_root(runtime_dir)
    payloads = list_pending_runtime_updates(runtime_dir)
    if not payloads:
        seen: set[str] = set()
        ordered: list[str] = []
        for component_name in [*normalized_components, *sanitized_components]:
            if component_name not in seen:
                seen.add(component_name)
                ordered.append(component_name)
        return ordered

    components_root = runtime_components_root(runtime_dir)
    components_root.mkdir(parents=True, exist_ok=True)
    applied: list[str] = []
    for payload in payloads:
        payload_dir = Path(str(payload.get('payload_dir') or '')).resolve()
        manifest_path = Path(str(payload.get('manifest_path') or '')).resolve()
        component_name = normalize_runtime_component_id(str(payload.get('component') or 'runtime'))
        if not payload_dir.exists() or not payload_dir.is_dir():
            continue
        live_root = runtime_component_live_root(runtime_dir, component_name)
        cleanup_patterns = tuple(payload.get('cleanup_patterns') or ())
        _clear_legacy_runtime_entries(runtime_dir, cleanup_patterns)
        if live_root.exists():
            shutil.rmtree(live_root, ignore_errors=False)
        live_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(payload_dir), str(live_root))
        try:
            shutil.rmtree(payload_dir.parent, ignore_errors=False)
        except OSError:
            pass
        if manifest_path.exists():
            manifest_path.unlink(missing_ok=True)
        applied.append(component_name)

    pending_root = runtime_pending_root(runtime_dir)
    if pending_root.exists():
        try:
            if not any(pending_root.iterdir()):
                pending_root.rmdir()
        except OSError:
            pass
    seen: set[str] = set()
    ordered: list[str] = []
    for component_name in [*normalized_components, *sanitized_components, *applied]:
        if component_name not in seen:
            seen.add(component_name)
            ordered.append(component_name)
    return ordered


def clear_runtime_component_registry(runtime_dir: Path, component_id: str) -> list[Path]:
    runtime_dir = Path(runtime_dir)
    component_name = normalize_runtime_component_id(component_id)
    registry_path = runtime_component_registry_path(runtime_dir)
    registry = load_runtime_component_registry(runtime_dir)
    removed_roots: list[Path] = []
    payload = registry.pop(component_name, None)
    if payload:
        raw_path = str(payload.get('path') or '').strip()
        if raw_path:
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = runtime_dir / raw_path
            removed_roots.append(candidate.resolve())
    if registry:
        registry_path.write_text(json.dumps(registry, ensure_ascii=True, indent=2), encoding='utf-8')
    else:
        registry_path.unlink(missing_ok=True)
    return removed_roots


def ensure_runtime_layout(runtime_dir: Path) -> list[str]:
    return apply_pending_runtime_updates(runtime_dir)
