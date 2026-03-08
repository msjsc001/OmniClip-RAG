from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message="Unable to find acceptable character detection dependency.*")

from .config import AppConfig, ensure_data_paths, load_config, normalize_vault_path, save_config
from .formatting import format_bytes, format_space_report
from .service import OmniClipService, WATCHDOG_AVAILABLE


def main() -> int:
    _configure_stdio()
    parser = _build_parser()
    args = parser.parse_args()
    global_paths = ensure_data_paths(getattr(args, "data_dir", None))

    if args.command == "init":
        data_paths = ensure_data_paths(getattr(args, "data_dir", None), args.vault)
        config = AppConfig(
            vault_path=normalize_vault_path(args.vault),
            vault_paths=[normalize_vault_path(args.vault)],
            data_root=str(data_paths.global_root),
        )
        _apply_runtime_overrides(config, args)
        save_config(config, data_paths)
        print(f"已初始化配置，全局数据目录：{data_paths.global_root}")
        print(f"当前笔记库工作区：{data_paths.root}")
        return 0

    config = load_config(global_paths)
    if config is None:
        if getattr(args, "vault", None):
            data_paths = ensure_data_paths(getattr(args, "data_dir", None), args.vault)
            config = AppConfig(
                vault_path=normalize_vault_path(args.vault),
                vault_paths=[normalize_vault_path(args.vault)],
                data_root=str(data_paths.global_root),
            )
            _apply_runtime_overrides(config, args)
            save_config(config, data_paths)
        else:
            parser.error("请先运行 init，或为当前命令显式传入 --vault。")

    if getattr(args, "vault", None):
        config.vault_path = normalize_vault_path(args.vault)
    if config.vault_path and config.vault_path not in config.vault_paths:
        config.vault_paths.insert(0, config.vault_path)
    changed = _apply_runtime_overrides(config, args)
    data_paths = ensure_data_paths(getattr(args, "data_dir", None), config.vault_path)
    if changed or getattr(args, "vault", None):
        save_config(config, data_paths)

    service = OmniClipService(config, data_paths)
    try:
        if args.command == "estimate-space":
            report = service.estimate_space()
            print(format_space_report(report))
            return 0 if report.can_proceed else 2
        if args.command == "bootstrap-model":
            if not args.skip_preflight:
                report = service.estimate_space()
                print(format_space_report(report))
                if not report.can_proceed and not args.force:
                    print("空间或前置条件检查未通过，已停止模型预热。可用 --force 强制继续，或先调整配置/清理空间。")
                    return 2
            result = service.bootstrap_model()
            print(
                "模型预热完成："
                f"backend={result.get('backend')} | "
                f"model={result.get('model')} | "
                f"dimension={result.get('dimension')} | "
                f"cache={format_bytes(result.get('cache_bytes', 0))}"
            )
            return 0
        if args.command == "index":
            if not args.skip_preflight:
                report = service.estimate_space()
                print(format_space_report(report))
                if not report.can_proceed and not args.force:
                    print("空间或前置条件检查未通过，已停止建库。可用 --force 强制继续，或先调整配置/清理空间。")
                    return 2
            stats = service.rebuild_index()
            print(f"建索引完成：{stats['files']} 个文件，{stats['chunks']} 个片段，{stats['refs']} 条引用")
            return 0
        if args.command == "query":
            hits, context_pack = service.query(args.query_text, limit=args.limit, copy_result=args.copy)
            print(context_pack)
            print(f"命中 {len(hits)} 条结果")
            return 0
        if args.command == "watch":
            mode = "polling" if args.polling or not WATCHDOG_AVAILABLE else "watchdog"
            print(f"监听模式：{mode}")
            service.watch(interval=args.interval, force_polling=args.polling)
            return 0
        if args.command == "status":
            stats = service.store.stats()
            print(f"Vault: {config.vault_dir}")
            print(f"Global data: {data_paths.global_root}")
            print(f"Workspace data: {data_paths.root}")
            print(f"Files: {stats['files']}")
            print(f"Chunks: {stats['chunks']}")
            print(f"Refs: {stats['refs']}")
            print(f"Vector backend: {config.vector_backend}")
            latest_preflight = service.store.fetch_latest_preflight()
            if latest_preflight is not None:
                print(
                    "Last preflight: "
                    f"{latest_preflight['risk_level']} | "
                    f"required={format_bytes(latest_preflight['required_free_bytes'])} | "
                    f"available={format_bytes(latest_preflight['available_free_bytes'])}"
                )
            return 0
        if args.command == "open-data-dir":
            service.open_data_dir()
            print(data_paths.root)
            return 0
        if args.command == "clear-data":
            clear_all = args.all
            service.clear_data(
                clear_index=clear_all or args.index,
                clear_logs=clear_all or args.logs,
                clear_cache=clear_all or args.cache,
                clear_exports=clear_all or args.exports,
            )
            print("清理完成")
            return 0
    finally:
        service.close()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omniclip")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="初始化配置")
    init_parser.add_argument("--vault", required=True, help="笔记库根目录")
    init_parser.add_argument("--data-dir", help="自定义数据目录")
    _add_vector_args(init_parser)

    estimate_parser = subparsers.add_parser("estimate-space", help="预估首轮建库需要的硬盘空间")
    estimate_parser.add_argument("--vault", help="覆盖配置中的笔记库根目录")
    estimate_parser.add_argument("--data-dir", help="自定义数据目录")
    _add_vector_args(estimate_parser)

    bootstrap_parser = subparsers.add_parser("bootstrap-model", help="预热并下载向量模型到本地缓存")
    bootstrap_parser.add_argument("--vault", help="覆盖配置中的笔记库根目录")
    bootstrap_parser.add_argument("--data-dir", help="自定义数据目录")
    bootstrap_parser.add_argument("--skip-preflight", action="store_true", help="跳过空间预检查")
    bootstrap_parser.add_argument("--force", action="store_true", help="即使预检查失败也继续模型预热")
    _add_vector_args(bootstrap_parser)

    index_parser = subparsers.add_parser("index", help="全量建索引")
    index_parser.add_argument("--vault", help="覆盖配置中的笔记库根目录")
    index_parser.add_argument("--data-dir", help="自定义数据目录")
    index_parser.add_argument("--skip-preflight", action="store_true", help="跳过空间预检查")
    index_parser.add_argument("--force", action="store_true", help="即使预检查失败也继续建库")
    _add_vector_args(index_parser)

    query_parser = subparsers.add_parser("query", help="查询笔记")
    query_parser.add_argument("query_text", help="查询内容")
    query_parser.add_argument("--limit", type=int, help="返回数量")
    query_parser.add_argument("--copy", action="store_true", help="复制上下文到剪贴板")
    query_parser.add_argument("--vault", help="覆盖配置中的笔记库根目录")
    query_parser.add_argument("--data-dir", help="自定义数据目录")
    _add_vector_args(query_parser)

    watch_parser = subparsers.add_parser("watch", help="监听并热更新")
    watch_parser.add_argument("--interval", type=float, default=2.0, help="轮询/批处理秒数")
    watch_parser.add_argument("--polling", action="store_true", help="强制使用轮询模式")
    watch_parser.add_argument("--vault", help="覆盖配置中的笔记库根目录")
    watch_parser.add_argument("--data-dir", help="自定义数据目录")
    _add_vector_args(watch_parser)

    status_parser = subparsers.add_parser("status", help="查看索引状态")
    status_parser.add_argument("--vault", help="覆盖配置中的笔记库根目录")
    status_parser.add_argument("--data-dir", help="自定义数据目录")
    _add_vector_args(status_parser)

    open_parser = subparsers.add_parser("open-data-dir", help="打开数据目录")
    open_parser.add_argument("--data-dir", help="自定义数据目录")
    open_parser.add_argument("--vault", help="指定要打开哪个笔记库对应的数据目录")

    clear_parser = subparsers.add_parser("clear-data", help="分类清理数据")
    clear_parser.add_argument("--all", action="store_true", help="清理全部可清理数据")
    clear_parser.add_argument("--index", action="store_true", help="清理索引")
    clear_parser.add_argument("--logs", action="store_true", help="清理日志")
    clear_parser.add_argument("--cache", action="store_true", help="清理缓存")
    clear_parser.add_argument("--exports", action="store_true", help="清理导出的上下文包")
    clear_parser.add_argument("--data-dir", help="自定义数据目录")
    clear_parser.add_argument("--vault", help="指定要清理哪个笔记库对应的数据目录")

    return parser


def _add_vector_args(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--vector-backend", help="例如 disabled / lancedb")
    subparser.add_argument("--vector-model", help="例如 BAAI/bge-m3")
    subparser.add_argument("--vector-runtime", help="例如 torch / onnx")
    subparser.add_argument("--vector-device", help="例如 cpu / cuda")
    subparser.add_argument("--vector-local-files-only", action="store_true", help="只使用本地模型缓存")


def _apply_runtime_overrides(config: AppConfig, args: argparse.Namespace) -> bool:
    changed = False
    for field_name in ("vector_backend", "vector_model", "vector_runtime", "vector_device"):
        value = getattr(args, field_name, None)
        if value:
            setattr(config, field_name, value)
            changed = True
    if getattr(args, "vector_local_files_only", False):
        config.vector_local_files_only = True
        changed = True
    return changed



def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue


if __name__ == "__main__":
    raise SystemExit(main())
