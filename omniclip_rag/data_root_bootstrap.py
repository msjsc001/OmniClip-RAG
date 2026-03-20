from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .config import default_bootstrap_root, default_data_root, legacy_default_data_root, probe_data_root
from .errors import ActiveDataRootUnavailableError


BOOTSTRAP_SCHEMA_VERSION = 2
BOOTSTRAP_FILENAME = "bootstrap.json"
BOOTSTRAP_PATH_ENV = "OMNICLIP_BOOTSTRAP_PATH"
DATA_ROOT_OVERRIDE_ENV = "OMNICLIP_DATA_ROOT"


@dataclass(slots=True)
class BootstrapRecord:
    schema_version: int = BOOTSTRAP_SCHEMA_VERSION
    active_data_root: str = ""
    known_data_roots: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResolvedActiveDataRoot:
    path: Path
    source: str
    bootstrap_file: Path
    known_data_roots: tuple[str, ...] = ()
    bootstrap_state: str = "missing"
    bootstrap_error: str = ""


def bootstrap_file_path() -> Path:
    override = str(os.environ.get(BOOTSTRAP_PATH_ENV) or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return default_bootstrap_root().resolve() / BOOTSTRAP_FILENAME


def read_bootstrap_pointer() -> dict[str, object] | None:
    record = read_bootstrap_record()
    if record is None:
        return None
    return {
        "schema_version": record.schema_version,
        "active_data_root": record.active_data_root,
        "known_data_roots": list(record.known_data_roots),
    }


def read_bootstrap_record() -> BootstrapRecord | None:
    pointer_path = bootstrap_file_path()
    if not pointer_path.exists():
        return None
    payload = _load_bootstrap_payload(pointer_path)
    return _parse_bootstrap_payload(payload)


def write_bootstrap_pointer(
    active_data_root: str | Path,
    *,
    known_data_roots: list[str] | tuple[str, ...] | None = None,
) -> Path:
    normalized_root = Path(str(active_data_root).strip()).expanduser().resolve()
    pointer_path = bootstrap_file_path()
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_bootstrap_record()
    merged_roots = _merge_known_data_roots(
        [normalized_root],
        _implicit_known_data_roots(active_root=normalized_root),
        list(known_data_roots) if known_data_roots is not None else list(existing.known_data_roots if existing else []),
    )
    record = BootstrapRecord(
        schema_version=BOOTSTRAP_SCHEMA_VERSION,
        active_data_root=str(normalized_root),
        known_data_roots=merged_roots,
    )
    pointer_path.write_text(
        json.dumps(
            {
                "schema_version": record.schema_version,
                "active_data_root": record.active_data_root,
                "known_data_roots": record.known_data_roots,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return pointer_path


def resolve_active_data_root(explicit_data_root: str | Path | None = None) -> ResolvedActiveDataRoot:
    pointer_path = bootstrap_file_path()
    explicit_value = str(explicit_data_root or "").strip()
    if explicit_value:
        return ResolvedActiveDataRoot(
            path=Path(explicit_value).expanduser().resolve(),
            source="explicit",
            bootstrap_file=pointer_path,
            known_data_roots=(str(Path(explicit_value).expanduser().resolve()),),
            bootstrap_state="explicit",
        )

    env_value = str(os.environ.get(DATA_ROOT_OVERRIDE_ENV) or "").strip()
    if env_value:
        return ResolvedActiveDataRoot(
            path=Path(env_value).expanduser().resolve(),
            source="env",
            bootstrap_file=pointer_path,
            known_data_roots=(str(Path(env_value).expanduser().resolve()),),
            bootstrap_state="env",
        )

    if not pointer_path.exists():
        default_root = default_data_root().resolve()
        return ResolvedActiveDataRoot(
            path=default_root,
            source="default",
            bootstrap_file=pointer_path,
            known_data_roots=tuple(_merge_known_data_roots([default_root], _implicit_known_data_roots(active_root=default_root))),
            bootstrap_state="missing",
        )

    try:
        payload = _load_bootstrap_payload(pointer_path)
        record = _parse_bootstrap_payload(payload)
    except ValueError as exc:
        default_root = default_data_root().resolve()
        return ResolvedActiveDataRoot(
            path=default_root,
            source="default",
            bootstrap_file=pointer_path,
            known_data_roots=tuple(_merge_known_data_roots([default_root], _implicit_known_data_roots(active_root=default_root))),
            bootstrap_state="invalid",
            bootstrap_error=str(exc),
        )

    active_root = Path(record.active_data_root).expanduser().resolve()
    known_roots = tuple(_merge_known_data_roots([active_root], record.known_data_roots, _implicit_known_data_roots(active_root=active_root)))
    return ResolvedActiveDataRoot(
        path=active_root,
        source="bootstrap",
        bootstrap_file=pointer_path,
        known_data_roots=known_roots,
        bootstrap_state="ready",
    )


def validate_active_data_root(resolved: ResolvedActiveDataRoot) -> ResolvedActiveDataRoot:
    if resolved.bootstrap_state == "invalid":
        raise ActiveDataRootUnavailableError(
            str(resolved.bootstrap_file),
            "bootstrap_invalid",
            source="bootstrap",
            detail=resolved.bootstrap_error,
        )
    allow_create = resolved.source == "default"
    probe = probe_data_root(resolved.path, allow_create=allow_create)
    if probe.state not in {"new", "existing"}:
        raise ActiveDataRootUnavailableError(
            str(probe.root),
            probe.reason or "active_data_root_unavailable",
            source=resolved.source,
            detail=probe.detail,
        )
    return ResolvedActiveDataRoot(
        path=probe.root,
        source=resolved.source,
        bootstrap_file=resolved.bootstrap_file,
        known_data_roots=resolved.known_data_roots,
        bootstrap_state=resolved.bootstrap_state,
        bootstrap_error=resolved.bootstrap_error,
    )


def resolve_and_validate_active_data_root(explicit_data_root: str | Path | None = None) -> ResolvedActiveDataRoot:
    return validate_active_data_root(resolve_active_data_root(explicit_data_root))


def known_data_roots(explicit_data_root: str | Path | None = None) -> list[str]:
    resolved = resolve_active_data_root(explicit_data_root)
    return list(_merge_known_data_roots([resolved.path], resolved.known_data_roots))


def _load_bootstrap_payload(pointer_path: Path) -> dict[str, object]:
    try:
        payload = json.loads(pointer_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"bootstrap-read-failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("bootstrap-not-object")
    return payload


def _parse_bootstrap_payload(payload: dict[str, object]) -> BootstrapRecord:
    try:
        schema_version = int(payload.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("bootstrap-schema-version-invalid") from exc
    if schema_version != BOOTSTRAP_SCHEMA_VERSION:
        raise ValueError(f"bootstrap-schema-version-unsupported:{schema_version}")
    active_data_root = str(payload.get("active_data_root") or "").strip()
    if not active_data_root:
        raise ValueError("bootstrap-active-data-root-missing")
    known_roots_raw = payload.get("known_data_roots") or []
    if not isinstance(known_roots_raw, list):
        raise ValueError("bootstrap-known-data-roots-invalid")
    normalized_active = str(Path(active_data_root).expanduser().resolve())
    known_roots = _merge_known_data_roots([normalized_active], known_roots_raw)
    return BootstrapRecord(
        schema_version=schema_version,
        active_data_root=normalized_active,
        known_data_roots=known_roots,
    )


def _merge_known_data_roots(*roots_groups: object) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for group in roots_groups:
        if isinstance(group, (str, Path)):
            values = [group]
        else:
            values = list(group or [])
        for value in values:
            raw = str(value or "").strip()
            if not raw:
                continue
            normalized = str(Path(raw).expanduser().resolve())
            if normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _implicit_known_data_roots(*, active_root: Path | None = None) -> list[str]:
    roots: list[Path] = []
    if active_root is not None:
        roots.append(Path(active_root).expanduser().resolve())
    default_root = default_data_root().resolve()
    legacy_root = legacy_default_data_root().resolve()
    for candidate in (default_root, legacy_root):
        if candidate == roots[0] if roots else False:
            continue
        if candidate.exists():
            roots.append(candidate)
    return [str(path) for path in roots]
