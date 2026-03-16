from __future__ import annotations

import gc
import tempfile
import time
import traceback
from dataclasses import asdict
from pathlib import Path


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
            import shutil
            shutil.rmtree(temp_root)
            return
        except PermissionError:
            gc.collect()
            time.sleep(0.2)
    import shutil
    shutil.rmtree(temp_root, ignore_errors=True)


def run_gpu_query_canary() -> dict[str, object]:
    """Run a zero-download GPU query canary through the real QueryService chain.

    Why: GPU verification must prove more than "CUDA exists". This canary reuses
    the same QueryService path as the desktop query flow, but swaps in tiny
    built-in torch backends so the validation stays deterministic and does not
    trigger any model downloads.
    """

    from .app_logging import shutdown_logging
    from .canary_backend import CANARY_RERANKER_MODEL_ID, CANARY_VECTOR_MODEL_ID
    from .config import AppConfig, ensure_data_paths
    from .service import OmniClipService

    service = None
    temp_root = Path(tempfile.mkdtemp(prefix='omniclip_gpu_canary_')).resolve()
    started_at = time.perf_counter()
    try:
        vault_dir, data_root = _gpu_canary_workspace(temp_root)
        paths = ensure_data_paths(str(data_root), str(vault_dir))
        config = AppConfig(
            vault_path=str(vault_dir),
            data_root=str(paths.global_root),
            query_limit=5,
            query_score_threshold=0.0,
            vector_backend='lancedb',
            vector_model=CANARY_VECTOR_MODEL_ID,
            vector_candidate_limit=12,
            vector_device='cuda',
            vector_runtime='torch',
            reranker_enabled=True,
            reranker_model=CANARY_RERANKER_MODEL_ID,
        )
        service = OmniClipService(config, paths)
        service.rebuild_index()
        result = service.query(
            '我的思维',
            limit=5,
            score_threshold=0.0,
            allowed_families=('markdown',),
            query_mode='hybrid',
            device_policy='require-cuda',
        )
        insights = result.insights
        query_stage = dict(insights.query_stage or {})
        reranker = asdict(insights.reranker) if insights.reranker is not None else None
        vector_actual_device = str(query_stage.get('vector_actual_device') or '')
        reranker_actual_device = str(query_stage.get('reranker_actual_device') or '')
        reranker_applied = bool(query_stage.get('reranker_applied'))
        reason = ''
        success = bool(result.hits)
        if not success:
            reason = 'no_hits'
        elif not vector_actual_device.startswith('cuda'):
            success = False
            reason = 'vector_not_cuda'
        elif reranker_applied and not reranker_actual_device.startswith('cuda'):
            success = False
            reason = 'reranker_not_cuda'
        return {
            'success': success,
            'state': 'verified' if success else 'failed',
            'reason': reason,
            'execution_error_class': '',
            'execution_error_message': '',
            'requested_device': 'cuda',
            'resolved_device': str(query_stage.get('vector_resolved_device') or 'cuda'),
            'actual_device': vector_actual_device,
            'reranker_actual_device': reranker_actual_device,
            'result_count': len(result.hits),
            'runtime_warnings': list(insights.runtime_warnings or ()),
            'query_plan': dict(insights.query_plan or {}),
            'query_fingerprint': dict(insights.query_fingerprint or {}),
            'query_stage': query_stage,
            'reranker': reranker,
            'top_hits': [
                {
                    'title': getattr(hit, 'title', ''),
                    'anchor': getattr(hit, 'anchor', ''),
                    'score': float(getattr(hit, 'score', 0.0) or 0.0),
                    'source_path': getattr(hit, 'source_path', ''),
                }
                for hit in result.hits[:5]
            ],
            'elapsed_ms': max(int((time.perf_counter() - started_at) * 1000), 0),
        }
    except Exception as exc:
        return {
            'success': False,
            'state': 'failed',
            'reason': 'gpu_query_canary_failed',
            'execution_error_class': exc.__class__.__name__,
            'execution_error_message': str(exc).strip() or exc.__class__.__name__,
            'traceback': traceback.format_exc(),
            'requested_device': 'cuda',
            'resolved_device': '',
            'actual_device': '',
            'reranker_actual_device': '',
            'result_count': 0,
            'runtime_warnings': [],
            'query_plan': {},
            'query_fingerprint': {},
            'query_stage': {},
            'reranker': None,
            'top_hits': [],
            'elapsed_ms': max(int((time.perf_counter() - started_at) * 1000), 0),
        }
    finally:
        if service is not None:
            service.close()
        shutdown_logging()
        _cleanup_temp_root(temp_root)
