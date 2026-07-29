from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import asdict, replace
from pathlib import Path

from ..runtime_canary import run_gpu_query_canary
from ..headless.bootstrap import apply_runtime_layout_if_needed as _shared_apply_runtime_layout_if_needed


def _apply_runtime_layout_if_needed() -> None:
    try:
        _shared_apply_runtime_layout_if_needed()
    except Exception:
        # Why: desktop startup must keep working even when runtime layout cleanup
        # hits a damaged installation. The query trace will capture the real cause.
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--ui', default='next')
    parser.add_argument('--selfcheck-query', action='store_true')
    parser.add_argument('--download-worker', action='store_true')
    parser.add_argument('--download-kind', default='')
    parser.add_argument('--repo-id', default='')
    parser.add_argument('--target-dir', default='')
    parser.add_argument('--hf-home', default='')
    parser.add_argument('--source', default='official')
    parser.add_argument('--log-path', default='')
    parser.add_argument('--pid-path', default='')
    parser.add_argument('--result-path', default='')
    parser.add_argument('--local-files-only', action='store_true')
    parser.add_argument('--vault', default='')
    parser.add_argument('--data-root', default='')
    parser.add_argument('--query', default='')
    parser.add_argument('--limit', type=int, default=30)
    parser.add_argument('--threshold', type=float, default=0.0)
    parser.add_argument('--output', default='')
    parser.add_argument('--query-mode', default='hybrid')
    parser.add_argument('--selfcheck-runtime', default='')
    args, _unknown = parser.parse_known_args(argv)
    _apply_runtime_layout_if_needed()
    if _should_run_runtime_selfcheck(args):
        return run_selfcheck_runtime(
            check_kind=args.selfcheck_runtime,
            output_path=args.output,
        )
    if _should_run_download_worker(args):
        return run_download_worker(
            download_kind=args.download_kind,
            repo_id=args.repo_id,
            target_dir=args.target_dir,
            hf_home=args.hf_home,
            download_source=args.source,
            log_path=args.log_path,
            pid_path=args.pid_path,
            result_path=args.result_path,
            local_files_only=bool(args.local_files_only),
        )
    if _should_run_selfcheck(args):
        return run_selfcheck_query(
            vault_path=args.vault,
            data_root=args.data_root,
            query_text=args.query,
            limit=args.limit,
            threshold=args.threshold,
            output_path=args.output,
            query_mode=args.query_mode,
        )
    return launch_desktop(args.ui)


def _selfcheck_paths_and_config(vault_path: str, data_root: str):
    from ..config import DataPaths, load_config, normalize_vault_path, workspace_id_for_vault

    normalized_vault = normalize_vault_path(vault_path)
    global_root = Path(data_root).expanduser().resolve() if str(data_root or '').strip() else None
    if global_root is None:
        raise RuntimeError('自检需要提供 --data-root 指向现有数据目录。')

    def existing_paths(target_vault: str) -> DataPaths:
        workspace_id = workspace_id_for_vault(target_vault)
        shared_root = global_root / 'shared'
        workspace_root = global_root / 'workspaces' / workspace_id
        return DataPaths(
            global_root=global_root,
            shared_root=shared_root,
            workspaces_dir=global_root / 'workspaces',
            workspace_id=workspace_id,
            root=workspace_root,
            state_dir=workspace_root / 'state',
            logs_dir=shared_root / 'logs',
            cache_dir=shared_root / 'cache',
            exports_dir=workspace_root / 'exports',
            config_file=global_root / 'config.json',
            sqlite_file=workspace_root / 'state' / 'omniclip.sqlite3',
        )

    probe_paths = existing_paths(normalized_vault)
    loaded = load_config(probe_paths)
    if loaded is None:
        raise RuntimeError('未找到配置文件，无法执行查询自检。')
    final_vault = normalized_vault or normalize_vault_path(getattr(loaded, 'vault_path', ''))
    if not final_vault:
        raise RuntimeError('当前没有可用的笔记库路径。')
    paths = existing_paths(final_vault)
    config = replace(loaded, vault_path=final_vault, data_root=str(global_root), query_limit=max(int(loaded.query_limit or 0), 1))
    return paths, config


def _selfcheck_output_path(output_path: str) -> Path:
    requested = str(output_path or '').strip()
    if requested:
        return Path(requested)
    return Path(tempfile.gettempdir()) / 'omniclip_query_selfcheck.json'


def _should_run_selfcheck(args: argparse.Namespace) -> bool:
    if not bool(getattr(args, 'selfcheck_query', False)):
        return False
    # Why: selfcheck is a diagnostic entrypoint. Frozen desktop launches must not
    # fall into it unless the caller clearly supplied selfcheck arguments.
    explicit = any(str(getattr(args, key, '') or '').strip() for key in ('vault', 'data_root', 'query', 'output'))
    if explicit:
        return True
    return os.environ.get('OMNICLIP_ALLOW_SELFCHECK', '').strip() == '1'


def _should_run_runtime_selfcheck(args: argparse.Namespace) -> bool:
    return bool(str(getattr(args, 'selfcheck_runtime', '') or '').strip())


def _should_run_download_worker(args: argparse.Namespace) -> bool:
    return bool(getattr(args, 'download_worker', False))


def _json_default(value: object):
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f'Object of type {value.__class__.__name__} is not JSON serializable')


def _write_selfcheck_payload(target: Path, payload: dict[str, object]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding='utf-8')


def _write_download_worker_payload(target: Path | None, payload: dict[str, object]) -> None:
    if target is None:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding='utf-8',
    )


def _ensure_download_worker_stdio(log_path: Path | None) -> list[object]:
    """
    Why: the frozen GUI EXE may enter worker mode with `sys.stderr` unset.
    Third-party download stacks such as ModelScope/tqdm assume a writable stream
    exists and crash with `'NoneType' object has no attribute 'write'` otherwise.
    """
    opened_streams: list[object] = []
    stdout_stream = getattr(sys, 'stdout', None)
    stderr_stream = getattr(sys, 'stderr', None)
    if stdout_stream is not None and getattr(stdout_stream, 'write', None) and stderr_stream is None:
        sys.stderr = stdout_stream
        return opened_streams
    if stderr_stream is not None and getattr(stderr_stream, 'write', None) and stdout_stream is None:
        sys.stdout = stderr_stream
        return opened_streams
    if (
        stdout_stream is not None
        and getattr(stdout_stream, 'write', None)
        and stderr_stream is not None
        and getattr(stderr_stream, 'write', None)
    ):
        return opened_streams
    fallback_path = log_path
    if fallback_path is None:
        fallback_path = Path.cwd() / 'download-worker-fallback.log'
    fallback_path.parent.mkdir(parents=True, exist_ok=True)
    fallback_stream = fallback_path.open('a', encoding='utf-8', buffering=1)
    opened_streams.append(fallback_stream)
    if stdout_stream is None or not getattr(stdout_stream, 'write', None):
        sys.stdout = fallback_stream
    if stderr_stream is None or not getattr(stderr_stream, 'write', None):
        sys.stderr = fallback_stream
    return opened_streams


def _download_worker_logger(log_path: Path | None):
    def emit(message: str) -> None:
        text_value = str(message or '').strip()
        if not text_value:
            return
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        lines = [f'[{timestamp}] {chunk.strip()}' for chunk in text_value.splitlines() if chunk.strip()]
        if not lines:
            return
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open('a', encoding='utf-8') as handle:
                handle.write('\n'.join(lines) + '\n')
        for line in lines:
            print(line, flush=True)

    return emit


def _directory_snapshot(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    if path.is_file():
        try:
            return int(path.stat().st_size), 1
        except OSError:
            return 0, 0
    total = 0
    files = 0
    for child in path.rglob('*'):
        try:
            if child.is_file():
                total += int(child.stat().st_size)
                files += 1
        except OSError:
            continue
    return total, files


def _format_download_bytes(value: int) -> str:
    size = max(int(value or 0), 0)
    units = ('B', 'KB', 'MB', 'GB', 'TB')
    index = 0
    scaled = float(size)
    while scaled >= 1024.0 and index < len(units) - 1:
        scaled /= 1024.0
        index += 1
    if index == 0:
        return f'{int(scaled)} {units[index]}'
    return f'{scaled:.1f} {units[index]}'


def _format_download_elapsed(seconds: float) -> str:
    total_seconds = max(int(seconds or 0), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f'{hours:02d}:{minutes:02d}:{secs:02d}'
    return f'{minutes:02d}:{secs:02d}'


def _start_download_heartbeat(
    *,
    emit,
    target_dir: Path,
    repo_cache_dir: Path,
    interval_seconds: float = 5.0,
) -> tuple[threading.Event, threading.Thread]:
    stop_event = threading.Event()

    def run() -> None:
        started_at = time.monotonic()
        last_change_at = started_at
        last_target_bytes, last_target_files = _directory_snapshot(target_dir)
        last_cache_bytes, last_cache_files = _directory_snapshot(repo_cache_dir)
        interval_label = f'{float(interval_seconds):g} 秒'

        def emit_snapshot() -> None:
            nonlocal last_change_at, last_target_bytes, last_target_files, last_cache_bytes, last_cache_files
            target_bytes, target_files = _directory_snapshot(target_dir)
            cache_bytes, cache_files = _directory_snapshot(repo_cache_dir)
            now = time.monotonic()
            target_delta_bytes = target_bytes - last_target_bytes
            target_delta_files = target_files - last_target_files
            cache_delta_bytes = cache_bytes - last_cache_bytes
            cache_delta_files = cache_files - last_cache_files
            if (
                target_delta_bytes != 0
                or cache_delta_bytes != 0
                or target_delta_files != 0
                or cache_delta_files != 0
            ):
                last_change_at = now
            elapsed = _format_download_elapsed(now - started_at)
            since_change = _format_download_elapsed(now - last_change_at)
            if target_bytes <= 0 and cache_bytes <= 0 and target_files <= 0 and cache_files <= 0:
                emit(
                    f'下载心跳：已用时 {elapsed}；当前仍在等待远端响应或首个文件，'
                    f'目标目录 0 B / 0 个文件；HF 缓存 0 B / 0 个文件；最近实际进展 {since_change} 前。'
                )
            elif (
                target_delta_bytes == 0
                and cache_delta_bytes == 0
                and target_delta_files == 0
                and cache_delta_files == 0
            ):
                emit(
                    f'下载心跳：已用时 {elapsed}；最近 {interval_label} 暂无新增文件或字节，'
                    f'目标目录 {_format_download_bytes(target_bytes)} / {target_files} 个文件；'
                    f'HF 缓存 {_format_download_bytes(cache_bytes)} / {cache_files} 个文件；'
                    f'最近实际进展 {since_change} 前。'
                )
            else:
                target_delta = (
                    f'目标目录 +{_format_download_bytes(max(target_delta_bytes, 0))} / '
                    f'+{max(target_delta_files, 0)} 个文件'
                )
                cache_delta = (
                    f'HF 缓存 +{_format_download_bytes(max(cache_delta_bytes, 0))} / '
                    f'+{max(cache_delta_files, 0)} 个文件'
                )
                emit(
                    f'下载心跳：已用时 {elapsed}；最近 {interval_label} 新增 {target_delta}，{cache_delta}；'
                    f'当前累计 目标目录 {_format_download_bytes(target_bytes)} / {target_files} 个文件；'
                    f'HF 缓存 {_format_download_bytes(cache_bytes)} / {cache_files} 个文件；'
                    f'最近实际进展 {since_change} 前。'
                )
            last_target_bytes, last_target_files = target_bytes, target_files
            last_cache_bytes, last_cache_files = cache_bytes, cache_files

        emit_snapshot()
        while not stop_event.wait(interval_seconds):
            emit_snapshot()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return stop_event, thread


def _probe_download_repo(repo_id: str, *, download_source: str, emit) -> None:
    from ..vector_index import _model_download_attempt_chain, _model_download_attempt_label, _model_download_endpoint

    normalized_source = (download_source or 'official').strip().lower() or 'official'
    attempt_chain = _model_download_attempt_chain(normalized_source, repo_id)
    first_attempt = attempt_chain[0] if attempt_chain else 'hf-official'
    attempt_label = _model_download_attempt_label(first_attempt)
    if first_attempt == 'modelscope':
        try:
            from modelscope.hub.api import HubApi
        except Exception as exc:
            emit(f'仓库元数据预检跳过：无法导入 ModelScope HubApi（{exc.__class__.__name__}: {exc}）')
            return
        try:
            api = HubApi()
            files = api.get_model_files(repo_id, recursive=True)
            emit(f'仓库元数据预检成功：{attempt_label} 检测到 {len(list(files or []))} 个远端条目。')
        except Exception as exc:
            emit(f'仓库元数据预检失败，但会继续尝试下载：{exc.__class__.__name__}: {exc}')
        return
    endpoint = _model_download_endpoint(normalized_source)
    try:
        from huggingface_hub import HfApi
    except Exception as exc:
        emit(f'仓库元数据预检跳过：无法导入 huggingface_hub.HfApi（{exc.__class__.__name__}: {exc}）')
        return
    try:
        api = HfApi(endpoint=endpoint)
        info = api.model_info(repo_id)
        sibling_count = len(list(getattr(info, 'siblings', []) or []))
        emit(f'仓库元数据预检成功：{attempt_label} 检测到 {sibling_count} 个远端条目。')
    except Exception as exc:
        emit(f'仓库元数据预检失败，但会继续尝试下载：{exc.__class__.__name__}: {exc}')


def run_download_worker(
    *,
    download_kind: str,
    repo_id: str,
    target_dir: str,
    hf_home: str,
    download_source: str,
    log_path: str,
    pid_path: str,
    result_path: str,
    local_files_only: bool,
) -> int:
    from ..errors import RuntimeDependencyError
    from ..vector_index import (
        _model_download_source_label,
        model_download_attempt_labels,
        download_hf_repo_snapshot,
        hf_repo_cache_dir,
    )

    normalized_kind = (download_kind or 'vector').strip().lower() or 'vector'
    normalized_source = (download_source or 'official').strip().lower() or 'official'
    target = Path(str(target_dir or '')).expanduser().resolve()
    hf_home_dir = Path(str(hf_home or '')).expanduser().resolve()
    log_target = Path(str(log_path or '')).expanduser().resolve() if str(log_path or '').strip() else None
    pid_target = Path(str(pid_path or '')).expanduser().resolve() if str(pid_path or '').strip() else None
    result_target = Path(str(result_path or '')).expanduser().resolve() if str(result_path or '').strip() else None
    stdio_streams = _ensure_download_worker_stdio(log_target)
    emit = _download_worker_logger(log_target)
    try:
        if pid_target is not None:
            pid_target.parent.mkdir(parents=True, exist_ok=True)
            pid_target.write_text(str(os.getpid()), encoding='utf-8')
        _write_download_worker_payload(
            result_target,
            {
                'state': 'running',
                'ok': None,
                'pid': os.getpid(),
                'kind': normalized_kind,
                'repo_id': repo_id,
                'target_dir': str(target),
                'hf_home': str(hf_home_dir),
                'source': normalized_source,
            },
        )
        repo_cache_dir = hf_repo_cache_dir(hf_home_dir, repo_id)
        emit(f'下载 worker 已启动，PID={os.getpid()}')
        emit(f'下载类型：{normalized_kind}')
        emit(f'仓库：{repo_id}')
        emit(f'目标目录：{target}')
        emit(f'HF_HOME：{hf_home_dir}')
        emit(f'仓库缓存目录：{repo_cache_dir}')
        emit(f'下载源：{_model_download_source_label(normalized_source)}')
        attempt_labels = model_download_attempt_labels(normalized_source, repo_id)
        if attempt_labels:
            emit('自动下载链路：' + ' -> '.join(attempt_labels))
        _probe_download_repo(repo_id, download_source=normalized_source, emit=emit)
        emit('下载心跳监控已启动：每 5 秒刷新一次目标目录与缓存大小。')
        heartbeat_stop, heartbeat_thread = _start_download_heartbeat(
            emit=emit,
            target_dir=target,
            repo_cache_dir=repo_cache_dir,
        )
        try:
            download_hf_repo_snapshot(
                repo_id=repo_id,
                local_dir=target,
                hf_home_dir=hf_home_dir,
                local_files_only=bool(local_files_only),
                download_source=normalized_source,
                download_log=emit,
                missing_dependency_message='当前还缺少 huggingface-hub 运行时，暂时不能下载模型缓存。',
            )
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1.5)
            final_target_bytes, final_target_files = _directory_snapshot(target)
            final_cache_bytes, final_cache_files = _directory_snapshot(repo_cache_dir)
            emit(
                f'下载心跳监控已停止：目标目录 {_format_download_bytes(final_target_bytes)} / {final_target_files} 个文件；'
                f'HF 缓存 {_format_download_bytes(final_cache_bytes)} / {final_cache_files} 个文件。'
            )
        emit('下载 worker 已完成。')
        _write_download_worker_payload(
            result_target,
            {
                'state': 'success',
                'ok': True,
                'pid': os.getpid(),
                'kind': normalized_kind,
                'repo_id': repo_id,
                'target_dir': str(target),
                'hf_home': str(hf_home_dir),
                'source': normalized_source,
                'repo_cache_dir': str(repo_cache_dir),
            },
        )
        return 0
    except RuntimeDependencyError as exc:
        emit(f'下载失败：{exc}')
        _write_download_worker_payload(
            result_target,
            {
                'state': 'failed',
                'ok': False,
                'pid': os.getpid(),
                'kind': normalized_kind,
                'repo_id': repo_id,
                'target_dir': str(target),
                'hf_home': str(hf_home_dir),
                'source': normalized_source,
                'error': str(exc).strip() or exc.__class__.__name__,
                'traceback': traceback.format_exc(),
            },
        )
        return 1
    except Exception as exc:
        emit(f'下载失败：{exc.__class__.__name__}: {exc}')
        emit(traceback.format_exc())
        _write_download_worker_payload(
            result_target,
            {
                'state': 'failed',
                'ok': False,
                'pid': os.getpid(),
                'kind': normalized_kind,
                'repo_id': repo_id,
                'target_dir': str(target),
                'hf_home': str(hf_home_dir),
                'source': normalized_source,
                'error': str(exc).strip() or exc.__class__.__name__,
                'traceback': traceback.format_exc(),
            },
        )
        return 1
    finally:
        for stream in stdio_streams:
            try:
                stream.flush()
            except Exception:
                pass


def run_selfcheck_query(
    *,
    vault_path: str,
    data_root: str,
    query_text: str,
    limit: int,
    threshold: float,
    output_path: str,
    query_mode: str,
) -> int:
    from ..service import OmniClipService
    from ..vector_index import detect_acceleration, inspect_runtime_environment

    target = _selfcheck_output_path(output_path)
    if not str(query_text or '').strip():
        payload = {
            'ok': False,
            'error': '--selfcheck-query 需要提供 --query。',
            'traceback': '',
        }
        _write_selfcheck_payload(target, payload)
        return 2

    service = None
    try:
        paths, loaded_config = _selfcheck_paths_and_config(vault_path, data_root)
        config = replace(
            loaded_config,
            query_limit=max(int(limit or loaded_config.query_limit or 1), 1),
            query_score_threshold=max(float(threshold or 0.0), 0.0),
        )
        # Why: 自检必须复用真正的工作区路径，而不是重新指向别处，否则会误判“算法坏了”。
        service = OmniClipService(config, paths)
        normalized_query_mode = str(query_mode or 'hybrid').strip() or 'hybrid'
        modes = (
            ('lexical-only', 'lexical-only'),
            ('vector-only', 'vector-only'),
            ('hybrid_no_rerank', 'hybrid_no_rerank'),
            ('hybrid', 'hybrid'),
        ) if normalized_query_mode.lower() == 'suite' else ((normalized_query_mode, normalized_query_mode),)
        mode_payloads = []
        last_result = None
        last_insights = None
        last_reranker = None
        for mode_label, mode_value in modes:
            result = service.query(
                query_text,
                limit=config.query_limit,
                score_threshold=config.query_score_threshold,
                allowed_families=('markdown',),
                query_mode=mode_value,
            )
            insights = getattr(result, 'insights', None)
            reranker = getattr(insights, 'reranker', None) if insights is not None else None
            mode_payloads.append({
                'query_mode': mode_label,
                'result_count': len(getattr(result, 'hits', []) or []),
                'runtime_warnings': list(getattr(insights, 'runtime_warnings', ()) or ()) if insights is not None else [],
                'query_plan': getattr(insights, 'query_plan', {}) if insights is not None else {},
                'query_fingerprint': getattr(insights, 'query_fingerprint', {}) if insights is not None else {},
                'query_stage': getattr(insights, 'query_stage', {}) if insights is not None else {},
                'reranker': asdict(reranker) if reranker is not None else None,
                'top_hits': [
                    {
                        'title': getattr(hit, 'title', ''),
                        'anchor': getattr(hit, 'anchor', ''),
                        'score': float(getattr(hit, 'score', 0.0) or 0.0),
                        'reason': getattr(hit, 'reason', ''),
                        'source_family': getattr(hit, 'source_family', ''),
                        'source_label': getattr(hit, 'source_label', ''),
                    }
                    for hit in list(getattr(result, 'hits', []) or [])[:10]
                ],
            })
            last_result = result
            last_insights = insights
            last_reranker = reranker
        insights = last_insights
        reranker = last_reranker
        result = last_result
        payload = {
            'ok': True,
            'vault_path': config.vault_path,
            'data_root': str(paths.global_root),
            'query_text': query_text,
            'query_limit': config.query_limit,
            'query_score_threshold': config.query_score_threshold,
            'runtime': inspect_runtime_environment(),
            'acceleration': detect_acceleration(force_refresh=True),
            'vector_index_type': type(service.vector_index).__name__,
            'vector_index_status': service._vector_index_status(),
            'result_count': len(getattr(result, 'hits', []) or []),
            'runtime_warnings': list(getattr(insights, 'runtime_warnings', ()) or ()) if insights is not None else [],
            'query_mode': normalized_query_mode,
            'query_plan': getattr(insights, 'query_plan', {}) if insights is not None else {},
            'query_fingerprint': getattr(insights, 'query_fingerprint', {}) if insights is not None else {},
            'query_stage': getattr(insights, 'query_stage', {}) if insights is not None else {},
            'mode_payloads': mode_payloads,
            'reranker': asdict(reranker) if reranker is not None else None,
            'top_hits': [
                {
                    'title': getattr(hit, 'title', ''),
                    'anchor': getattr(hit, 'anchor', ''),
                    'score': float(getattr(hit, 'score', 0.0) or 0.0),
                    'reason': getattr(hit, 'reason', ''),
                    'source_family': getattr(hit, 'source_family', ''),
                    'source_label': getattr(hit, 'source_label', ''),
                }
                for hit in list(getattr(result, 'hits', []) or [])[:10]
            ],
        }
        _write_selfcheck_payload(target, payload)
        return 0
    except Exception as exc:
        payload = {
            'ok': False,
            'error': str(exc).strip() or exc.__class__.__name__,
            'traceback': traceback.format_exc(),
        }
        try:
            _write_selfcheck_payload(target, payload)
        except Exception:
            fallback = Path(tempfile.gettempdir()) / 'omniclip_query_selfcheck_error.json'
            _write_selfcheck_payload(fallback, payload)
        return 1
    finally:
        if service is not None:
            service.close()


def _gpu_canary_workspace(temp_root: Path) -> tuple[Path, Path]:
    vault_dir = temp_root / 'vault'
    data_root = temp_root / 'data'
    vault_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    (vault_dir / '思维框架.md').write_text(
        '\n'.join(
            (
                '- 我的思维框架',
                '  - 我常用一套自己的思考框架来拆问题',
                '  - 遇到复杂任务时会先分析再行动',
            )
        ),
        encoding='utf-8',
    )
    (vault_dir / '任务记录.md').write_text(
        '\n'.join(
            (
                '- 今日任务',
                '  - 购买牛奶',
                '  - 清理购物清单',
            )
        ),
        encoding='utf-8',
    )
    (vault_dir / '分析笔记.md').write_text(
        '\n'.join(
            (
                '- 问题拆解笔记',
                '  - 先分析问题结构，再决定解决路径',
                '  - 这套思考方法适合长期记录',
            )
        ),
        encoding='utf-8',
    )
    return vault_dir, data_root


def _cleanup_temp_root(temp_root: Path) -> None:
    if not temp_root.exists():
        return
    for _ in range(5):
        try:
            shutil.rmtree(temp_root)
            return
        except PermissionError:
            gc.collect()
            time.sleep(0.2)
    shutil.rmtree(temp_root, ignore_errors=True)


def run_selfcheck_runtime(*, check_kind: str, output_path: str) -> int:
    from ..vector_index import detect_acceleration, inspect_runtime_environment, probe_runtime_gpu_execution

    target = _selfcheck_output_path(output_path)
    normalized_kind = str(check_kind or 'suite').strip().lower() or 'suite'
    try:
        runtime_payload = inspect_runtime_environment()
        acceleration_payload = detect_acceleration(force_refresh=True)
        if normalized_kind == 'gpu-smoke':
            smoke = probe_runtime_gpu_execution(force_refresh=True)
            payload = {
                'ok': bool(smoke.get('success')),
                'check_kind': normalized_kind,
                'runtime': runtime_payload,
                'acceleration': acceleration_payload,
                'gpu_smoke': smoke,
            }
        elif normalized_kind == 'gpu-query-canary':
            canary = run_gpu_query_canary()
            payload = {
                'ok': bool(canary.get('success')),
                'check_kind': normalized_kind,
                'runtime': runtime_payload,
                'acceleration': acceleration_payload,
                'gpu_query_canary': canary,
            }
        elif normalized_kind == 'suite':
            smoke = probe_runtime_gpu_execution(force_refresh=True)
            canary = run_gpu_query_canary()
            payload = {
                'ok': bool(smoke.get('success')) and bool(canary.get('success')),
                'check_kind': normalized_kind,
                'runtime': runtime_payload,
                'acceleration': acceleration_payload,
                'gpu_smoke': smoke,
                'gpu_query_canary': canary,
            }
        else:
            payload = {
                'ok': False,
                'check_kind': normalized_kind,
                'error': f'Unsupported runtime selfcheck kind: {normalized_kind}',
            }
        _write_selfcheck_payload(target, payload)
        return 0 if payload.get('ok') else 1
    except Exception as exc:
        payload = {
            'ok': False,
            'check_kind': normalized_kind,
            'error': str(exc).strip() or exc.__class__.__name__,
            'traceback': traceback.format_exc(),
        }
        _write_selfcheck_payload(target, payload)
        return 1


def launch_desktop(ui_mode: str = 'next') -> int:
    normalized = str(ui_mode or 'next').strip().lower() or 'next'
    if normalized != 'next':
        print('Legacy UI has been permanently retired. OmniClip RAG now starts the Qt desktop only.', file=sys.stderr, flush=True)
    try:
        from ..ui_next_qt.app import main as qt_main
    except Exception as exc:
        print(f'Qt UI import failed: {exc}', file=sys.stderr, flush=True)
        traceback.print_exc()
        print('Qt desktop startup failed. Please repair or reinstall the app.', file=sys.stderr, flush=True)
        return 1
    return qt_main()

