from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from dataclasses import asdict, replace
from pathlib import Path


def _apply_runtime_layout_if_needed() -> None:
    if not getattr(sys, 'frozen', False):
        return
    try:
        from ..runtime_layout import ensure_runtime_layout
        ensure_runtime_layout(Path(sys.executable).resolve().parent / 'runtime')
    except Exception:
        # Why: desktop startup must keep working even when runtime layout cleanup
        # hits a damaged installation. The query trace will capture the real cause.
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--ui', default='next')
    parser.add_argument('--selfcheck-query', action='store_true')
    parser.add_argument('--vault', default='')
    parser.add_argument('--data-root', default='')
    parser.add_argument('--query', default='')
    parser.add_argument('--limit', type=int, default=30)
    parser.add_argument('--threshold', type=float, default=0.0)
    parser.add_argument('--output', default='')
    parser.add_argument('--query-mode', default='hybrid')
    args, _unknown = parser.parse_known_args(argv)
    _apply_runtime_layout_if_needed()
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


def _json_default(value: object):
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f'Object of type {value.__class__.__name__} is not JSON serializable')


def _write_selfcheck_payload(target: Path, payload: dict[str, object]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding='utf-8')


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
