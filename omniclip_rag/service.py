from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .clipboard import copy_text
from .config import AppConfig, DataPaths
from .models import SearchHit, SpaceEstimate
from .parser import BLOCK_REF_RE, EMBED_RE, PAGE_REF_RE
from .preflight import estimate_storage_for_vault
from .storage import MetadataStore
from .vector_index import create_vector_index

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    WATCHDOG_AVAILABLE = True
except ImportError:
    FileSystemEvent = object  # type: ignore[assignment]
    FileSystemEventHandler = object  # type: ignore[assignment]
    Observer = None  # type: ignore[assignment]
    WATCHDOG_AVAILABLE = False


IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
TAG_RE = re.compile(r"(?<!\w)#([\w\-\u4e00-\u9fff/]+)")
MAX_RENDER_DEPTH = 4
MAX_EXPANDED_LENGTH = 480
WATCH_DEBOUNCE_SECONDS = 0.8
REBUILD_STATE_VERSION = 1


class OmniClipService:
    def __init__(self, config: AppConfig, paths: DataPaths) -> None:
        self.config = config
        self.paths = paths
        self.store = MetadataStore(paths.sqlite_file)
        self.vector_index = create_vector_index(config, paths)
        self._rebuild_state_file = self.paths.state_dir / 'rebuild_state.json'

    def close(self) -> None:
        self.store.close()

    def save_runtime_config(self) -> None:
        payload = asdict(self.config)
        self.paths.config_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    # Why: 真正影响速度的是目录遍历是否剪枝，而不是后面解析器里再过滤一次。
    def scan_vault(self) -> list[Path]:
        if not self.config.vault_path:
            return []
        ignore = set(self.config.ignore_dirs)
        files: list[Path] = []
        for root, dirnames, filenames in os.walk(self.config.vault_dir, topdown=True):
            dirnames[:] = [name for name in dirnames if name not in ignore]
            current_root = Path(root)
            for filename in filenames:
                if not filename.lower().endswith('.md'):
                    continue
                files.append((current_root / filename).resolve())
        return sorted(files)

    def estimate_space(self) -> SpaceEstimate:
        report = estimate_storage_for_vault(self.config, self.paths, files=self.scan_vault())
        self.store.record_preflight(report, str(self.config.vault_dir))
        return report

    def bootstrap_model(self) -> dict[str, object]:
        result = self.vector_index.warmup()
        result['cache_bytes'] = _directory_size(self.paths.cache_dir / 'models')
        return result

    def rebuild_index(
        self,
        *,
        resume: bool = False,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        pause_event: threading.Event | None = None,
    ) -> dict[str, int]:
        from .parser import parse_markdown_file

        files = self.scan_vault()
        manifest = self._build_file_manifest(files)
        state = self._read_rebuild_state() if resume else None
        if state is None or not self._can_resume_rebuild_state(state, manifest):
            if resume and self._rebuild_state_file.exists():
                self.discard_pending_rebuild()
            self._start_fresh_rebuild_state(manifest)
            self.store.reset_all()
            self.vector_index.reset()
            state = self._read_rebuild_state() or self._start_fresh_rebuild_state(manifest)
        else:
            state['file_manifest'] = manifest
            state['total_files'] = len(manifest)
            self._write_rebuild_state(state)

        completed_paths = set(state.get('completed_paths', []))
        readable_paths = list(dict.fromkeys(state.get('readable_paths', [])))
        skipped_paths = list(dict.fromkeys(state.get('skipped_paths', [])))
        duplicate_block_ids = int(state.get('duplicate_block_ids', 0))
        total_files = len(manifest)

        for path in files:
            _wait_if_paused(pause_event)
            relative_path = path.relative_to(self.config.vault_dir).as_posix()
            if relative_path in completed_paths:
                continue
            try:
                parsed = parse_markdown_file(self.config.vault_dir, path)
            except OSError:
                skipped_paths.append(relative_path)
            else:
                duplicate_block_ids += len(self.store.replace_file(parsed))
                readable_paths.append(relative_path)
            completed_paths.add(relative_path)
            state.update(
                {
                    'phase': 'indexing',
                    'completed_paths': sorted(completed_paths),
                    'readable_paths': list(dict.fromkeys(readable_paths)),
                    'skipped_paths': sorted(set(skipped_paths)),
                    'duplicate_block_ids': duplicate_block_ids,
                    'current_path': relative_path,
                    'updated_at': _utc_now(),
                }
            )
            self._write_rebuild_state(state)
            _emit_progress(
                on_progress,
                {
                    'stage': 'indexing',
                    'current': len(completed_paths),
                    'total': total_files,
                    'current_path': relative_path,
                    'duplicate_block_ids': duplicate_block_ids,
                },
            )

        readable_paths = list(dict.fromkeys(readable_paths))
        _wait_if_paused(pause_event)
        state.update({'phase': 'rendering', 'updated_at': _utc_now(), 'current_path': ''})
        self._write_rebuild_state(state)
        _emit_progress(on_progress, {'stage': 'rendering', 'current': len(readable_paths), 'total': len(readable_paths)})
        self._refresh_rendered(readable_paths, pause_event=pause_event)

        _wait_if_paused(pause_event)
        documents = self.store.fetch_vector_documents()
        state.update({'phase': 'vectorizing', 'updated_at': _utc_now(), 'current_path': ''})
        self._write_rebuild_state(state)
        _emit_progress(on_progress, {'stage': 'vectorizing', 'current': 0, 'total': len(documents)})

        def emit_vector_progress(progress: dict[str, object]) -> None:
            state['updated_at'] = _utc_now()
            self._write_rebuild_state(state)
            _emit_progress(on_progress, progress)

        self.vector_index.rebuild(documents, on_progress=emit_vector_progress, pause_event=pause_event)

        stats = {**self.store.stats(), 'duplicate_block_ids': duplicate_block_ids}
        self._clear_rebuild_state()
        return stats

    def reindex_paths(self, changed_relative_paths: list[str], deleted_relative_paths: list[str]) -> dict[str, int]:
        from .parser import parse_markdown_file

        impacted_paths = set(deleted_relative_paths)
        impacted_paths.update(changed_relative_paths)
        impacted_block_ids = self.store.get_block_ids_for_paths(impacted_paths)
        impacted_chunk_ids = self.store.get_chunk_ids_for_paths(impacted_paths)
        dependent_paths = self.store.get_transitive_dependent_paths(impacted_block_ids)

        if impacted_paths:
            self.store.delete_files(impacted_paths)
        if impacted_chunk_ids:
            self.vector_index.delete(impacted_chunk_ids)

        new_block_ids: set[str] = set()
        readable_changed_paths: list[str] = []
        duplicate_block_ids = 0
        for relative_path in changed_relative_paths:
            absolute_path = self.config.vault_dir / relative_path
            if not absolute_path.exists():
                continue
            try:
                parsed = parse_markdown_file(self.config.vault_dir, absolute_path)
            except OSError:
                continue
            duplicate_block_ids += len(self.store.replace_file(parsed))
            readable_changed_paths.append(relative_path)
            new_block_ids.update(chunk.block_id for chunk in parsed.chunks if chunk.block_id)

        affected_paths = set(impacted_paths) | dependent_paths | set(readable_changed_paths)
        if new_block_ids:
            affected_paths |= self.store.get_transitive_dependent_paths(new_block_ids)
        if affected_paths:
            affected_list = sorted(affected_paths)
            self._refresh_rendered(affected_list)
            self.vector_index.upsert(self.store.fetch_vector_documents(affected_list))
        return {**self.store.stats(), 'duplicate_block_ids': duplicate_block_ids}

    def query(self, query_text: str, limit: int | None = None, copy_result: bool = False) -> tuple[list[SearchHit], str]:
        limit = limit or self.config.query_limit
        candidate_limit = max(limit * 8, 24)
        storage_candidates = self.store.search_candidates(query_text, candidate_limit)
        vector_candidates = {item.chunk_id: item.score for item in self.vector_index.search(query_text, self.config.vector_candidate_limit)}

        if storage_candidates:
            hits = self._rank_candidates(query_text, storage_candidates, vector_candidates)
        else:
            rows = self.store.fetch_all_rendered_chunks()
            hits = self._rank_candidates(query_text, rows, vector_candidates)

        sliced_hits = hits[:limit]
        context_pack = self.compose_context_pack(query_text, sliced_hits)
        if copy_result:
            copy_text(context_pack)
        export_name = f"context_{int(time.time())}.md"
        (self.paths.exports_dir / export_name).write_text(context_pack, encoding='utf-8')
        return sliced_hits, context_pack

    def compose_context_pack(self, query_text: str, hits: list[SearchHit]) -> str:
        pages: list[str] = []
        seen_pages: set[str] = set()
        for hit in hits:
            if hit.title in seen_pages:
                continue
            seen_pages.add(hit.title)
            pages.append(hit.title)
        lines = [
            '# OmniClip Context Pack',
            '',
            f'用户问题：{query_text}',
            '',
            '## 相关页面',
        ]
        if pages:
            for index, page in enumerate(pages, start=1):
                lines.append(f'{index}. {page}')
        else:
            lines.append('1. 当前未命中高置信内容')
        lines.extend(['', '## 命中片段'])
        if hits:
            for index, hit in enumerate(hits, start=1):
                lines.append(f'### {index}. {hit.title}')
                lines.append(f'- 路径：{hit.source_path}')
                lines.append(f'- 语义路径：{hit.anchor}')
                lines.append(f'- 片段：{hit.rendered_text}')
                lines.append('')
        else:
            lines.append('- 没有找到足够相关的内容。')
            lines.append('')
        lines.extend(
            [
                '## 使用协议',
                '[系统级防幻觉与逆向检索指令]',
                '你只能基于以上本地笔记片段回答，不能虚构我未提供的本地概念。',
                '如果上下文不足，你必须停止发散，并明确输出：',
                '【本地上下文不足：请在 OmniClip 中检索关键词："关键词1", "关键词2"】',
            ]
        )
        return '\n'.join(lines).strip() + '\n'

    def watch(self, interval: float | None = None, force_polling: bool = False) -> None:
        stop_event = threading.Event()
        self.watch_until_stopped(stop_event, interval=interval, force_polling=force_polling)

    def watch_until_stopped(
        self,
        stop_event: threading.Event,
        interval: float | None = None,
        force_polling: bool = False,
        on_update: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        interval = interval or self.config.poll_interval_seconds
        if not force_polling and WATCHDOG_AVAILABLE:
            self._watch_with_watchdog(interval, stop_event, on_update)
            return
        self._watch_with_polling(interval, stop_event, on_update)

    def status_snapshot(self) -> dict[str, object]:
        latest = self.store.fetch_latest_preflight()
        stats = self.store.stats()
        latest_preflight = None
        if latest is not None:
            latest_preflight = {
                'risk_level': latest['risk_level'],
                'required_free_bytes': latest['required_free_bytes'],
                'available_free_bytes': latest['available_free_bytes'],
                'run_at': latest['run_at'],
            }
        pending = self.pending_rebuild()
        resolved_vault = str(self.config.vault_dir) if self.config.vault_path else ''
        return {
            'vault_path': resolved_vault,
            'data_root': str(self.paths.global_root),
            'shared_root': str(self.paths.shared_root),
            'workspace_root': str(self.paths.root),
            'workspace_id': self.paths.root.name,
            'vector_backend': self.config.vector_backend,
            'stats': stats,
            'latest_preflight': latest_preflight,
            'watchdog_available': WATCHDOG_AVAILABLE,
            'pending_rebuild': pending,
        }

    def pending_rebuild(self) -> dict[str, object] | None:
        state = self._read_rebuild_state()
        if state is None:
            return None
        completed = len(state.get('completed_paths', []))
        return {
            'phase': state.get('phase', 'indexing'),
            'completed': completed,
            'total': int(state.get('total_files', 0)),
            'started_at': state.get('started_at', ''),
            'updated_at': state.get('updated_at', ''),
            'current_path': state.get('current_path', ''),
        }

    def discard_pending_rebuild(self) -> None:
        self.vector_index.reset()
        self.store.reset_all()
        self._clear_rebuild_state()

    def open_data_dir(self) -> None:
        import os as _os

        _os.startfile(self.paths.root)  # type: ignore[attr-defined]

    def open_exports_dir(self) -> None:
        import os as _os

        _os.startfile(self.paths.exports_dir)  # type: ignore[attr-defined]

    def open_vault_dir(self) -> None:
        import os as _os

        _os.startfile(self.config.vault_dir)  # type: ignore[attr-defined]

    def clear_data(
        self,
        clear_index: bool = False,
        clear_logs: bool = False,
        clear_cache: bool = False,
        clear_exports: bool = False,
    ) -> None:
        if clear_index:
            self.vector_index.reset()
            self.store.close()
            if self.paths.sqlite_file.exists():
                self.paths.sqlite_file.unlink()
            self._clear_rebuild_state()
            self.store = MetadataStore(self.paths.sqlite_file)
        if clear_logs:
            _clear_directory(self.paths.logs_dir)
        if clear_cache:
            _clear_directory(self.paths.cache_dir)
        if clear_exports:
            _clear_directory(self.paths.exports_dir)

    def _rank_candidates(self, query_text: str, rows, vector_candidates: dict[str, float]) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for row in rows:
            fts_rank = row['fts_rank'] if 'fts_rank' in row.keys() else None
            like_hits = row['like_hits'] if 'like_hits' in row.keys() else 0
            score = _score_query(query_text, row['title'], row['anchor'], row['rendered_text'])
            score += _score_fts_rank(fts_rank)
            score += float(like_hits or 0) * 8.0
            score += vector_candidates.get(row['chunk_id'], 0.0) * 20.0
            if score <= 0:
                continue
            hits.append(
                SearchHit(
                    score=score,
                    title=row['title'],
                    anchor=row['anchor'],
                    source_path=row['source_path'],
                    rendered_text=row['rendered_text'],
                    chunk_id=row['chunk_id'],
                )
            )
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits

    def _watch_with_polling(
        self,
        interval: float,
        stop_event: threading.Event,
        on_update: Callable[[dict[str, object]], None] | None,
    ) -> None:
        previous = self._snapshot()
        while not stop_event.wait(interval):
            current = self._snapshot()
            changed, deleted = _diff_snapshot(previous, current)
            if changed or deleted:
                stats = self.reindex_paths(changed, deleted)
                _emit_watch_update(on_update, 'polling', changed, deleted, stats)
            previous = current

    def _watch_with_watchdog(
        self,
        interval: float,
        stop_event: threading.Event,
        on_update: Callable[[dict[str, object]], None] | None,
    ) -> None:
        handler = _VaultEventHandler(self.config.vault_dir, set(self.config.ignore_dirs))
        observer = Observer()
        observer.schedule(handler, str(self.config.vault_dir), recursive=True)
        observer.start()
        try:
            while not stop_event.wait(interval):
                changed, deleted = handler.pop_due_changes(WATCH_DEBOUNCE_SECONDS)
                if changed or deleted:
                    stats = self.reindex_paths(changed, deleted)
                    _emit_watch_update(on_update, 'watchdog', changed, deleted, stats)
        finally:
            observer.stop()
            observer.join(timeout=5)

    def _snapshot(self) -> dict[str, tuple[float, int]]:
        snapshot: dict[str, tuple[float, int]] = {}
        for path in self.scan_vault():
            stat = path.stat()
            snapshot[path.relative_to(self.config.vault_dir).as_posix()] = (stat.st_mtime, stat.st_size)
        return snapshot

    def _refresh_rendered(self, relative_paths: list[str], pause_event: threading.Event | None = None) -> None:
        block_lookup = self.store.fetch_block_lookup()
        rows = self.store.fetch_render_rows(relative_paths)
        payloads: list[tuple[str, str]] = []
        for row in rows:
            _wait_if_paused(pause_event)
            payloads.append((row['chunk_id'], _render_row(row, block_lookup)))
        self.store.update_rendered_chunks(payloads)

    def _build_file_manifest(self, files: list[Path]) -> dict[str, dict[str, float | int]]:
        manifest: dict[str, dict[str, float | int]] = {}
        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            manifest[path.relative_to(self.config.vault_dir).as_posix()] = {'mtime': stat.st_mtime, 'size': stat.st_size}
        return manifest

    def _start_fresh_rebuild_state(self, manifest: dict[str, dict[str, float | int]]) -> dict[str, object]:
        state = {
            'version': REBUILD_STATE_VERSION,
            'vault_path': str(self.config.vault_dir),
            'vector_backend': self.config.vector_backend,
            'vector_model': self.config.vector_model,
            'vector_runtime': self.config.vector_runtime,
            'vector_device': self.config.vector_device,
            'started_at': _utc_now(),
            'updated_at': _utc_now(),
            'phase': 'indexing',
            'total_files': len(manifest),
            'file_manifest': manifest,
            'completed_paths': [],
            'readable_paths': [],
            'skipped_paths': [],
            'duplicate_block_ids': 0,
            'current_path': '',
        }
        self._write_rebuild_state(state)
        return state

    def _can_resume_rebuild_state(self, state: dict[str, object], manifest: dict[str, dict[str, float | int]]) -> bool:
        if not state:
            return False
        if int(state.get('version', 0) or 0) != REBUILD_STATE_VERSION:
            return False
        if state.get('vault_path') != str(self.config.vault_dir):
            return False
        for key, expected in (
            ('vector_backend', self.config.vector_backend),
            ('vector_model', self.config.vector_model),
            ('vector_runtime', self.config.vector_runtime),
            ('vector_device', self.config.vector_device),
        ):
            if (state.get(key) or '') != (expected or ''):
                return False
        return state.get('file_manifest') == manifest

    def _read_rebuild_state(self) -> dict[str, object] | None:
        if not self._rebuild_state_file.exists():
            return None
        try:
            return json.loads(self._rebuild_state_file.read_text(encoding='utf-8'))
        except Exception:
            return None

    def _write_rebuild_state(self, state: dict[str, object]) -> None:
        self._rebuild_state_file.parent.mkdir(parents=True, exist_ok=True)
        self._rebuild_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')

    def _clear_rebuild_state(self) -> None:
        if self._rebuild_state_file.exists():
            try:
                self._rebuild_state_file.unlink()
            except OSError:
                pass


class _VaultEventHandler(FileSystemEventHandler):
    def __init__(self, vault_dir: Path, ignore_dirs: set[str]) -> None:
        super().__init__()
        self.vault_dir = vault_dir.resolve()
        self.ignore_dirs = ignore_dirs
        self._changed: dict[str, float] = {}
        self._deleted: dict[str, float] = {}
        self._lock = threading.Lock()

    def on_any_event(self, event: FileSystemEvent) -> None:
        if getattr(event, 'is_directory', False):
            return
        src_rel = self._to_relative_path(Path(event.src_path))
        dest_rel = self._to_relative_path(Path(getattr(event, 'dest_path', ''))) if getattr(event, 'dest_path', None) else None

        with self._lock:
            if src_rel:
                self._record_event(src_rel, getattr(event, 'event_type', 'modified'))
            if dest_rel:
                self._record_event(dest_rel, 'created')

    def pop_due_changes(self, debounce_seconds: float) -> tuple[list[str], list[str]]:
        deadline = time.time() - debounce_seconds
        with self._lock:
            changed = sorted(path for path, ts in self._changed.items() if ts <= deadline)
            deleted = sorted(path for path, ts in self._deleted.items() if ts <= deadline)
            for path in changed:
                self._changed.pop(path, None)
            for path in deleted:
                self._deleted.pop(path, None)
        changed = [path for path in changed if path not in deleted]
        return changed, deleted

    def _record_event(self, relative_path: str, event_type: str) -> None:
        if event_type in {'deleted', 'moved'}:
            self._deleted[relative_path] = time.time()
            self._changed.pop(relative_path, None)
            return
        self._changed[relative_path] = time.time()
        self._deleted.pop(relative_path, None)

    def _to_relative_path(self, path: Path) -> str | None:
        if not path:
            return None
        try:
            resolved = path.resolve()
        except OSError:
            return None
        if resolved.suffix.lower() != '.md':
            return None
        try:
            relative = resolved.relative_to(self.vault_dir).as_posix()
        except ValueError:
            return None
        if any(part in self.ignore_dirs for part in Path(relative).parts):
            return None
        return relative


def _render_row(row, block_lookup) -> str:
    page_properties = json.loads(row['page_properties_json'] or '{}')
    chunk_properties = json.loads(row['properties_json'] or '{}')
    raw_text = row['raw_text'] or ''
    block_id = row['block_id']
    visited = {block_id} if block_id else set()
    expanded = _expand_refs(raw_text, block_lookup, depth=0, visited=visited)
    expanded = _normalize_markup(expanded)

    sections = [row['title']]
    if page_properties:
        sections.append('页面属性: ' + _format_properties(page_properties))
    if row['anchor'] and row['anchor'] != row['title']:
        sections.append('语义路径: ' + row['anchor'])
    if chunk_properties:
        sections.append('块属性: ' + _format_properties(chunk_properties))
    if expanded:
        sections.append(expanded)
    return '\n'.join(section.strip() for section in sections if section and section.strip())


def _expand_refs(text: str, block_lookup, depth: int, visited: set[str]) -> str:
    if depth >= MAX_RENDER_DEPTH:
        return _truncate(text)

    def replace_embed(match: re.Match[str]) -> str:
        return _resolve_block_ref(match.group(1), block_lookup, depth + 1, visited, embed=True)

    def replace_block(match: re.Match[str]) -> str:
        return _resolve_block_ref(match.group(1), block_lookup, depth + 1, visited, embed=False)

    text = EMBED_RE.sub(replace_embed, text)
    text = BLOCK_REF_RE.sub(replace_block, text)
    return text


def _resolve_block_ref(block_id: str, block_lookup, depth: int, visited: set[str], embed: bool) -> str:
    if block_id in visited:
        return f'[循环引用:{block_id}]'
    target = block_lookup.get(block_id)
    if target is None:
        return f'[缺失引用:{block_id}]'
    next_visited = set(visited)
    next_visited.add(block_id)
    target_text = _expand_refs(target['raw_text'] or '', block_lookup, depth, next_visited)
    target_text = _normalize_markup(target_text)
    prefix = target['anchor'] or target['title']
    merged = f'{prefix}: {target_text}'.strip(': ')
    limit = MAX_EXPANDED_LENGTH if embed else MAX_EXPANDED_LENGTH // 2
    return _truncate(merged, limit=limit)


def _normalize_markup(text: str) -> str:
    normalized = PAGE_REF_RE.sub(r'\1', text)
    normalized = IMAGE_RE.sub(lambda match: match.group(1) or Path(match.group(2)).name, normalized)
    normalized = LINK_RE.sub(r'\1', normalized)
    normalized = TAG_RE.sub(r'\1', normalized)
    normalized = re.sub(r'\s+', ' ', normalized)
    return normalized.strip()


def _format_properties(properties: dict[str, str]) -> str:
    return '; '.join(f"{key}: {_normalize_markup(value)}" for key, value in properties.items())


def _truncate(text: str, limit: int = MAX_EXPANDED_LENGTH) -> str:
    normalized = re.sub(r'\s+', ' ', text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + '…'


def _score_query(query_text: str, title: str, anchor: str, rendered_text: str) -> float:
    normalized_query = query_text.strip().lower()
    if not normalized_query:
        return 0.0
    score = 0.0
    title_lower = title.lower()
    anchor_lower = anchor.lower()
    text_lower = rendered_text.lower()
    terms = [term for term in re.split(r'\s+', normalized_query) if term] or [normalized_query]

    if normalized_query in title_lower:
        score += 60
    if normalized_query in anchor_lower:
        score += 45
    if normalized_query in text_lower:
        score += 25

    for term in terms:
        if len(term) == 1 and term.isascii():
            continue
        if term in title_lower:
            score += 14
        if term in anchor_lower:
            score += 10
        score += min(text_lower.count(term), 6) * 4

    if _contains_cjk(normalized_query) and normalized_query in text_lower:
        score += 12

    return score


def _score_fts_rank(fts_rank: object) -> float:
    if fts_rank is None:
        return 0.0
    try:
        rank = float(fts_rank)
    except (TypeError, ValueError):
        return 0.0
    if rank <= 0:
        return 24.0
    return 24.0 / (1.0 + rank)


def _contains_cjk(text: str) -> bool:
    return any('\u4e00' <= char <= '\u9fff' for char in text)


def _diff_snapshot(previous: dict[str, tuple[float, int]], current: dict[str, tuple[float, int]]) -> tuple[list[str], list[str]]:
    changed: list[str] = []
    deleted: list[str] = []
    for relative_path, metadata in current.items():
        if relative_path not in previous or previous[relative_path] != metadata:
            changed.append(relative_path)
    for relative_path in previous:
        if relative_path not in current:
            deleted.append(relative_path)
    return changed, deleted


def _clear_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for item in directory.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for child in path.rglob('*'):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


def _wait_if_paused(pause_event: threading.Event | None) -> None:
    while pause_event is not None and pause_event.is_set():
        time.sleep(0.12)


def _emit_watch_update(on_update: Callable[[dict[str, object]], None] | None, mode: str, changed: list[str], deleted: list[str], stats: dict[str, int]) -> None:
    if on_update is None:
        return
    on_update({'mode': mode, 'changed': changed, 'deleted': deleted, 'stats': stats})


def _emit_progress(on_progress: Callable[[dict[str, object]], None] | None, payload: dict[str, object]) -> None:
    if on_progress is None:
        return
    on_progress(payload)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
