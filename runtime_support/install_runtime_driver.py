from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any


PROBES: dict[str, dict[str, object]] = {
    'torch': {'import_name': 'torch', 'required_entries': ['__init__.py']},
    'numpy': {
        'import_name': 'numpy',
        'required_entries': ['__init__.py', '_core'],
        'attribute': '__version__',
        'submodules': ['numpy.core.multiarray'],
    },
    'scipy': {'import_name': 'scipy', 'required_entries': ['__init__.py', 'linalg']},
    'sentence_transformers': {
        'import_name': 'sentence_transformers',
        'required_entries': ['__init__.py'],
        'attribute': 'SentenceTransformer',
    },
    'transformers': {
        'import_name': 'transformers',
        'required_entries': ['__init__.py', 'utils'],
        'submodules': ['transformers.utils'],
    },
    'huggingface_hub': {
        'import_name': 'huggingface_hub',
        'required_entries': ['__init__.py', 'hf_api.py'],
        'submodules': ['huggingface_hub.hf_api'],
    },
    'safetensors': {'import_name': 'safetensors', 'required_entries': ['__init__.py']},
    'lancedb': {'import_name': 'lancedb', 'required_entries': ['__init__.py']},
    'onnxruntime': {'import_name': 'onnxruntime', 'required_entries': ['__init__.py']},
    'pyarrow': {
        'import_name': 'pyarrow',
        'required_entries': ['__init__.py'],
        'submodules': ['pyarrow.lib'],
    },
    'pandas': {'import_name': 'pandas', 'required_entries': ['__init__.py'], 'attribute': '__version__'},
}


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _print(message: str) -> None:
    print(message, flush=True)


def _merged_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    merged_env = dict(os.environ)
    merged_env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    merged_env['PYTHONNOUSERSITE'] = '1'
    if extra:
        merged_env.update(extra)
    return merged_env


def _run_live(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 1800,
    heartbeat_message: str = '',
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors='replace',
        bufsize=1,
        env=_merged_env(env),
    )
    lines: list[str] = []
    started_at = time.monotonic()
    last_output_at = started_at
    last_heartbeat_at = started_at
    try:
        while True:
            if process.stdout is None:
                break
            line = process.stdout.readline()
            if line:
                last_output_at = time.monotonic()
                lines.append(line)
                text = line.rstrip()
                if text:
                    _print(text)
                continue
            if process.poll() is not None:
                break
            now = time.monotonic()
            if heartbeat_message and now - last_output_at >= 5 and now - last_heartbeat_at >= 5:
                _print(heartbeat_message)
                last_heartbeat_at = now
            if now - started_at > timeout:
                process.kill()
                raise TimeoutError(f'Command timed out after {timeout} seconds: {" ".join(args)}')
            time.sleep(0.2)
        if process.stdout is not None:
            remaining = process.stdout.read()
            if remaining:
                lines.append(remaining)
                for extra_line in remaining.splitlines():
                    if extra_line.strip():
                        _print(extra_line)
        return subprocess.CompletedProcess(args, process.returncode or 0, ''.join(lines), '')
    finally:
        if process.stdout is not None:
            process.stdout.close()


def _ensure_pip() -> None:
    try:
        import pip  # type: ignore # noqa: F401
    except Exception:
        completed = _run_live([sys.executable, '-Im', 'ensurepip', '--upgrade'], timeout=600)
        if completed.returncode != 0:
            raise RuntimeError(
                'Failed to bootstrap pip for bundled Python.\n'
                + (completed.stdout or '')
                + '\n'
                + (completed.stderr or '')
            )


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise RuntimeError(f'Invalid runtime manifest payload: {path}')
    payload.setdefault('manifest_path', str(path))
    return payload


def _source_sequence(manifest: dict[str, Any], requested_source: str) -> list[str]:
    profiles = dict(manifest.get('source_profiles') or {})
    normalized = (requested_source or 'official').strip().lower() or 'official'
    candidates = [normalized]
    for fallback in ('official', 'mirror'):
        if fallback not in candidates:
            candidates.append(fallback)
    return [candidate for candidate in candidates if candidate in profiles]


def _normalize_artifact_entry(entry: dict[str, Any]) -> dict[str, str]:
    filename = str(entry.get('filename') or '').strip()
    if not filename:
        raise RuntimeError(f'Runtime manifest artifact is missing filename: {entry!r}')
    requirement = str(entry.get('requirement') or '').strip()
    if not requirement:
        name = str(entry.get('name') or '').strip()
        version = str(entry.get('version') or '').strip()
        if not name or not version:
            raise RuntimeError(f'Runtime manifest artifact is missing requirement/name/version: {entry!r}')
        requirement = f'{name}=={version}'
    payload = {
        'name': str(entry.get('name') or '').strip(),
        'version': str(entry.get('version') or '').strip(),
        'requirement': requirement,
        'filename': filename,
        'sha256': str(entry.get('sha256') or '').strip().lower(),
        'source_key': str(entry.get('source_key') or 'pypi').strip() or 'pypi',
    }
    if not payload['sha256']:
        raise RuntimeError(f'Runtime manifest artifact is missing sha256: {entry!r}')
    return payload


def _manifest_artifacts(manifest: dict[str, Any]) -> list[dict[str, str]]:
    artifacts = [entry for entry in (manifest.get('artifacts') or []) if isinstance(entry, dict)]
    if artifacts:
        return [_normalize_artifact_entry(entry) for entry in artifacts]
    requirements = []
    for entry in (manifest.get('requirements') or []):
        if not isinstance(entry, dict):
            continue
        requirement = str(entry.get('requirement') or '').strip()
        if not requirement:
            continue
        requirements.append(
            {
                'name': str(entry.get('name') or '').strip(),
                'version': str(entry.get('version') or '').strip(),
                'requirement': requirement,
                'filename': '',
                'sha256': '',
                'source_key': str(entry.get('source_key') or 'pypi').strip() or 'pypi',
            }
        )
    return requirements


class DiagnosticsTracker:
    def __init__(self, path: Path, payload: dict[str, Any]) -> None:
        self.path = path
        self.payload = payload
        self.flush()

    def flush(self) -> None:
        _write_json(self.path, self.payload)

    def stage(self, name: str, *, total: int | None = None, current_artifact: str = '') -> None:
        now = _utc_now()
        stages = list(self.payload.get('stages') or [])
        if stages and 'completed_at' not in stages[-1]:
            stages[-1]['completed_at'] = now
        stages.append({'name': name, 'started_at': now})
        self.payload['stages'] = stages
        self.payload['current_stage'] = name
        self.payload['current_artifact'] = current_artifact
        if total is not None:
            self.payload['artifacts_total'] = int(total)
        self.flush()

    def set_current_artifact(self, artifact_name: str) -> None:
        self.payload['current_artifact'] = artifact_name
        self.flush()

    def update_counts(self, *, downloaded: int | None = None, verified: int | None = None) -> None:
        if downloaded is not None:
            self.payload['artifacts_downloaded'] = int(downloaded)
        if verified is not None:
            self.payload['artifacts_verified'] = int(verified)
        self.flush()

    def set_download_attempts(self, attempts: list[dict[str, Any]]) -> None:
        self.payload['download_attempts'] = attempts
        self.flush()

    def set_downloaded_artifacts(self, artifacts: list[dict[str, Any]]) -> None:
        self.payload['downloaded_artifacts'] = artifacts
        self.flush()

    def finish_ok(self) -> None:
        stages = list(self.payload.get('stages') or [])
        now = _utc_now()
        if stages and 'completed_at' not in stages[-1]:
            stages[-1]['completed_at'] = now
        self.payload['stages'] = stages
        self.payload['status'] = 'ok'
        self.payload['completed_at'] = now
        self.flush()

    def finish_error(self, exc: Exception) -> None:
        stages = list(self.payload.get('stages') or [])
        now = _utc_now()
        if stages and 'completed_at' not in stages[-1]:
            stages[-1]['completed_at'] = now
        self.payload['stages'] = stages
        self.payload['status'] = 'error'
        self.payload['completed_at'] = now
        self.payload['error_type'] = type(exc).__name__
        self.payload['error_message'] = str(exc)
        self.payload['traceback'] = traceback.format_exc()
        self.flush()


def _download_command(
    *,
    requirement: str,
    source_config: dict[str, Any],
    target_dir: Path,
) -> list[str]:
    find_links = [str(item).strip() for item in (source_config.get('find_links') or []) if str(item).strip()]
    index_url = str(source_config.get('index_url') or '').strip()
    command = [
        sys.executable,
        '-Im',
        'pip',
        'download',
        '--dest',
        str(target_dir),
        '--only-binary=:all:',
        '--prefer-binary',
        '--no-deps',
    ]
    if find_links and not index_url:
        command.append('--no-index')
    for find_link in find_links:
        command.extend(['--find-links', find_link])
    if index_url:
        command.extend(['--index-url', index_url])
    extra_index_urls = [str(item).strip() for item in (source_config.get('extra_index_urls') or []) if str(item).strip()]
    for extra_url in extra_index_urls:
        command.extend(['--extra-index-url', extra_url])
    command.append(requirement)
    return command


def _prune_wheelhouse(wheelhouse: Path, expected_filenames: set[str]) -> None:
    wheelhouse.mkdir(parents=True, exist_ok=True)
    for existing in wheelhouse.glob('*.whl'):
        if existing.name not in expected_filenames:
            existing.unlink(missing_ok=True)


def _download_artifact(
    artifact: dict[str, str],
    *,
    manifest: dict[str, Any],
    requested_source: str,
    wheelhouse: Path,
    tracker: DiagnosticsTracker,
    current_index: int,
    total: int,
    attempts: list[dict[str, Any]],
) -> str:
    filename = artifact['filename']
    expected_sha = artifact['sha256']
    artifact_path = wheelhouse / filename
    tracker.set_current_artifact(filename)
    if artifact_path.exists():
        actual_sha = _sha256(artifact_path)
        if actual_sha == expected_sha:
            _print(f"[下载 {current_index}/{total}] 复用已缓存 wheel：{filename}")
            return ''
        artifact_path.unlink(missing_ok=True)
    source_profiles = dict(manifest.get('source_profiles') or {})
    last_error = ''
    chosen_source = ''
    for candidate_source in _source_sequence(manifest, requested_source):
        profile = dict(source_profiles.get(candidate_source) or {})
        source_config = dict(profile.get(artifact['source_key']) or {})
        if not source_config:
            continue
        staging_dir = wheelhouse.parent / f"_download-{current_index:03d}-{candidate_source}"
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)
        _print(f"[下载 {current_index}/{total}] {filename} <- {candidate_source}")
        command = _download_command(requirement=artifact['requirement'], source_config=source_config, target_dir=staging_dir)
        completed = _run_live(
            command,
            timeout=7200,
            heartbeat_message=f"[下载 {current_index}/{total}] 仍在下载 {filename}，请稍候…",
        )
        attempt_payload = {
            'source_key': artifact['source_key'],
            'selected_source': requested_source,
            'attempt_source': candidate_source,
            'requirement': artifact['requirement'],
            'filename': filename,
            'command': completed.args,
            'returncode': completed.returncode,
            'stdout': completed.stdout,
        }
        attempts.append(attempt_payload)
        tracker.set_download_attempts(attempts)
        downloaded = staging_dir / filename
        if completed.returncode == 0 and downloaded.exists():
            shutil.move(str(downloaded), str(artifact_path))
            chosen_source = candidate_source
            shutil.rmtree(staging_dir, ignore_errors=True)
            break
        available = ', '.join(item.name for item in staging_dir.glob('*.whl'))
        last_error = (
            f"{(completed.stdout or '').strip()}\n"
            f"Expected wheel: {filename}. Downloaded: {available or 'none'}"
        ).strip()
        shutil.rmtree(staging_dir, ignore_errors=True)
    if not artifact_path.exists():
        raise RuntimeError(f'Failed to download runtime wheel {filename}: {last_error}')
    actual_sha = _sha256(artifact_path)
    if actual_sha != expected_sha:
        artifact_path.unlink(missing_ok=True)
        raise RuntimeError(
            f'Runtime wheel checksum mismatch for {filename}: expected {expected_sha}, got {actual_sha}'
        )
    return chosen_source


def _prepare_exact_wheelhouse(
    *,
    manifest: dict[str, Any],
    requested_source: str,
    wheelhouse: Path,
    tracker: DiagnosticsTracker,
) -> tuple[list[dict[str, Any]], str]:
    _ensure_pip()
    artifacts = _manifest_artifacts(manifest)
    exact_artifacts = [item for item in artifacts if item.get('filename')]
    if not exact_artifacts:
        raise RuntimeError('Runtime manifest does not define any exact wheel artifacts.')
    _prune_wheelhouse(wheelhouse, {item['filename'] for item in exact_artifacts})
    tracker.stage('download', total=len(exact_artifacts))
    attempts: list[dict[str, Any]] = []
    selected_sources: list[str] = []
    downloaded_count = 0
    verified_count = 0
    for index, artifact in enumerate(exact_artifacts, start=1):
        chosen_source = _download_artifact(
            artifact,
            manifest=manifest,
            requested_source=requested_source,
            wheelhouse=wheelhouse,
            tracker=tracker,
            current_index=index,
            total=len(exact_artifacts),
            attempts=attempts,
        )
        downloaded_count += 1
        tracker.update_counts(downloaded=downloaded_count)
        if chosen_source:
            selected_sources.append(chosen_source)
    tracker.stage('verify', total=len(exact_artifacts))
    verified_artifacts: list[dict[str, Any]] = []
    for index, artifact in enumerate(exact_artifacts, start=1):
        tracker.set_current_artifact(artifact['filename'])
        artifact_path = wheelhouse / artifact['filename']
        if not artifact_path.exists():
            raise RuntimeError(f"Runtime wheel is missing after download: {artifact['filename']}")
        actual_sha = _sha256(artifact_path)
        if actual_sha != artifact['sha256']:
            raise RuntimeError(
                f"Runtime wheel checksum mismatch for {artifact['filename']}: expected {artifact['sha256']}, got {actual_sha}"
            )
        verified_count += 1
        tracker.update_counts(downloaded=downloaded_count, verified=verified_count)
        verified_artifacts.append(
            {
                'filename': artifact_path.name,
                'size': artifact_path.stat().st_size,
                'sha256': actual_sha,
            }
        )
        _print(f"[校验 {index}/{len(exact_artifacts)}] {artifact_path.name}")
    tracker.set_downloaded_artifacts(verified_artifacts)
    unique_sources = list(dict.fromkeys(item for item in selected_sources if item))
    if not unique_sources:
        selected_source = requested_source
    elif len(unique_sources) == 1:
        selected_source = unique_sources[0]
    else:
        selected_source = 'mixed'
    return verified_artifacts, selected_source


def _offline_install(*, payload_target: Path, wheelhouse: Path, manifest: dict[str, Any], tracker: DiagnosticsTracker) -> subprocess.CompletedProcess[str]:
    artifacts = _manifest_artifacts(manifest)
    wheel_paths = [str((wheelhouse / artifact['filename']).resolve()) for artifact in artifacts if artifact.get('filename')]
    if not wheel_paths:
        raise RuntimeError('Runtime manifest does not provide installable wheel files.')
    tracker.stage('install', total=len(wheel_paths))
    _print(f"[安装] 正在把 {len(wheel_paths)} 个 wheel 离线安装到目标目录…")
    command = [
        sys.executable,
        '-Im',
        'pip',
        'install',
        '--upgrade',
        '--force-reinstall',
        '--ignore-installed',
        '--no-deps',
        '--no-index',
        '--target',
        str(payload_target),
        *wheel_paths,
    ]
    return _run_live(command, timeout=7200, heartbeat_message='[安装] 仍在写入 runtime 组件，请稍候…')


def _runtime_candidate_paths(runtime_dir: Path, dll_dir: Path | None) -> list[Path]:
    candidates = [
        runtime_dir,
        runtime_dir / 'bin',
        runtime_dir / 'pyarrow.libs',
        runtime_dir / 'numpy.libs',
        runtime_dir / 'scipy.libs',
        runtime_dir / 'torch' / 'lib',
    ]
    if dll_dir is not None:
        candidates.append(dll_dir)
    unique: list[Path] = []
    for candidate in candidates:
        if not candidate.exists():
            continue
        resolved = candidate.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _collect_origins(module: object) -> list[Path]:
    origins: list[Path] = []
    module_file = getattr(module, '__file__', None)
    if module_file:
        origins.append(Path(str(module_file)).resolve())
    module_path = getattr(module, '__path__', None)
    if module_path:
        for entry in module_path:
            origins.append(Path(str(entry)).resolve())
    unique: list[Path] = []
    for origin in origins:
        if origin not in unique:
            unique.append(origin)
    return unique


def _is_under_runtime(runtime_root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(runtime_root.resolve())
        return True
    except Exception:
        return False


def _validate_runtime(payload_target: Path, manifest: dict[str, Any], dll_dir: Path | None) -> None:
    required = [str(item).strip() for item in (manifest.get('required_modules') or []) if str(item).strip()]
    candidate_paths = _runtime_candidate_paths(payload_target, dll_dir)
    for candidate in reversed(candidate_paths):
        sys.path.insert(0, str(candidate))
        if hasattr(os, 'add_dll_directory'):
            try:
                os.add_dll_directory(str(candidate))
            except OSError:
                pass
    if candidate_paths:
        os.environ['PATH'] = os.pathsep.join([str(item) for item in candidate_paths] + [os.environ.get('PATH', '')])

    failures: list[str] = []
    for module_name in required:
        probe = PROBES.get(module_name, {'import_name': module_name, 'required_entries': [], 'submodules': []})
        package_root = payload_target / module_name
        for required_entry in probe.get('required_entries', []):
            if not (package_root / str(required_entry)).exists():
                failures.append(f'{module_name}: staged files are incomplete (missing {required_entry})')
                break
        else:
            try:
                module = importlib.import_module(str(probe['import_name']))
                origins = _collect_origins(module)
                if not getattr(module, '__file__', None):
                    raise ImportError('imported without __file__')
                if not origins or not all(_is_under_runtime(payload_target, origin) for origin in origins):
                    raise ImportError('module resolved outside staged runtime payload')
                required_attr = str(probe.get('attribute') or '').strip()
                if required_attr and not getattr(module, required_attr, None):
                    raise ImportError(f'missing required attribute {required_attr}')
                for submodule_name in probe.get('submodules', []):
                    submodule = importlib.import_module(str(submodule_name))
                    submodule_origins = _collect_origins(submodule)
                    if not submodule_origins or not all(_is_under_runtime(payload_target, origin) for origin in submodule_origins):
                        raise ImportError(f'{submodule_name} resolved outside staged runtime payload')
            except Exception as exc:
                failures.append(f'{module_name}: {type(exc).__name__}: {exc}')
    if failures:
        raise RuntimeError('Runtime validation failed:\n' + '\n'.join(failures))


def _write_bootstrap_metadata(payload_target: Path) -> dict[str, Any]:
    dll_dir = Path(sys.executable).resolve().parent
    payload = {
        'python_exe': str(Path(sys.executable).resolve()),
        'python_version': sys.version.split()[0],
        'stdlib': '',
        'platstdlib': '',
        'dll_dir': str(dll_dir),
        'python_home': str(Path(sys.executable).resolve().parent),
    }
    _write_json(payload_target / '_runtime_bootstrap.json', payload)
    _write_json(
        payload_target / '_runtime_validation.json',
        {
            'validated': True,
            'validated_at': _utc_now(),
        },
    )
    return payload


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Download and install OmniClip Runtime components into an isolated payload target.')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--component', required=True)
    parser.add_argument('--source', default='official')
    parser.add_argument('--runtime-root', required=True)
    parser.add_argument('--payload-target', required=True)
    parser.add_argument('--wheelhouse', required=True)
    parser.add_argument('--diagnostics-path', required=True)
    parser.add_argument('--result-path', required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    manifest_path = Path(args.manifest).resolve()
    runtime_root = Path(args.runtime_root).resolve()
    payload_target = Path(args.payload_target).resolve()
    wheelhouse = Path(args.wheelhouse).resolve()
    diagnostics_path = Path(args.diagnostics_path).resolve()
    result_path = Path(args.result_path).resolve()
    diagnostics: dict[str, Any] = {
        'reported_at': _utc_now(),
        'status': 'running',
        'profile': args.profile,
        'component': args.component,
        'requested_source': args.source,
        'runtime_root': str(runtime_root),
        'payload_target': str(payload_target),
        'wheelhouse': str(wheelhouse),
        'manifest_path': str(manifest_path),
        'python_exe': str(Path(sys.executable).resolve()),
        'python_version': sys.version.split()[0],
        'current_stage': 'initializing',
        'current_artifact': '',
        'artifacts_total': 0,
        'artifacts_downloaded': 0,
        'artifacts_verified': 0,
        'stages': [],
    }
    tracker = DiagnosticsTracker(diagnostics_path, diagnostics)
    try:
        tracker.stage('read_manifest')
        manifest = _load_manifest(manifest_path)
        diagnostics['manifest'] = {
            'schema_version': manifest.get('schema_version'),
            'profile': manifest.get('profile'),
            'component': manifest.get('component'),
            'python_tag': manifest.get('python_tag'),
            'platform_tag': manifest.get('platform_tag'),
        }
        tracker.flush()

        if payload_target.exists():
            shutil.rmtree(payload_target, ignore_errors=True)
        payload_target.mkdir(parents=True, exist_ok=True)
        wheelhouse.mkdir(parents=True, exist_ok=True)

        artifacts, selected_source = _prepare_exact_wheelhouse(
            manifest=manifest,
            requested_source=args.source,
            wheelhouse=wheelhouse,
            tracker=tracker,
        )
        diagnostics['selected_source'] = selected_source
        tracker.flush()

        completed = _offline_install(payload_target=payload_target, wheelhouse=wheelhouse, manifest=manifest, tracker=tracker)
        diagnostics['install_stdout'] = completed.stdout
        diagnostics['install_stderr'] = completed.stderr
        diagnostics['install_returncode'] = completed.returncode
        if completed.returncode != 0:
            raise RuntimeError(
                'Runtime offline installation failed.\n'
                + (completed.stdout or '')
                + '\n'
                + (completed.stderr or '')
            )

        tracker.stage('validate')
        _print('[验证] 正在校验模块是否能从目标 runtime 目录正确导入…')
        bootstrap_payload = _write_bootstrap_metadata(payload_target)
        _write_json(
            payload_target / '_runtime_manifest.json',
            {
                'profile': args.profile,
                'component': args.component,
                'selected_source': selected_source,
                'manifest_path': str(manifest_path),
                'requirements': list(manifest.get('requirements') or []),
                'artifacts': list(manifest.get('artifacts') or []),
                'downloaded_artifacts': artifacts,
            },
        )
        _validate_runtime(payload_target, manifest, Path(str(bootstrap_payload['dll_dir'])).resolve())
        result_payload = {
            'status': 'ok',
            'profile': args.profile,
            'component': args.component,
            'selected_source': selected_source,
            'runtime_root': str(runtime_root),
            'payload_target': str(payload_target),
            'wheelhouse': str(wheelhouse),
            'manifest_path': str(manifest_path),
            'cleanup_patterns': list(manifest.get('cleanup_patterns') or []),
            'required_modules': list(manifest.get('required_modules') or []),
            'downloaded_artifacts': artifacts,
            'bootstrap': bootstrap_payload,
        }
        tracker.finish_ok()
        _write_json(result_path, result_payload)
        _print('Runtime validation succeeded.')
        _print('Runtime component was installed successfully.')
        return 0
    except Exception as exc:
        tracker.finish_error(exc)
        _write_json(
            result_path,
            {
                'status': 'error',
                'error_type': type(exc).__name__,
                'error_message': str(exc),
                'diagnostics_path': str(diagnostics_path),
            },
        )
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
