from __future__ import annotations

import atexit
import faulthandler
import logging
from logging.handlers import RotatingFileHandler
import threading
import sys
from pathlib import Path

from .config import AppConfig, DataPaths, DEFAULT_LOG_FILE_SIZE_MB, ensure_data_paths, normalize_log_file_size_mb


LOG_FILE_NAME = 'omniclip.log'
FAULT_LOG_FILE_NAME = 'omniclip-fault.log'
LOG_BACKUP_COUNT = 3


_LOGGER_NAME = 'omniclip_rag'
_LOCK = threading.RLock()
_ACTIVE_HANDLER: RotatingFileHandler | None = None
_ACTIVE_LOG_PATH: Path | None = None
_ACTIVE_MAX_BYTES = 0
_FAULT_HANDLE = None
_EXCEPTION_HOOKS_INSTALLED = False
_PREVIOUS_SYS_EXCEPTHOOK = sys.excepthook
_PREVIOUS_THREAD_EXCEPTHOOK = getattr(threading, 'excepthook', None)


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(name or _LOGGER_NAME)


def configure_file_logging(paths: DataPaths, config: AppConfig | None = None) -> logging.Logger:
    log_config = config or AppConfig(vault_path='', data_root=str(paths.global_root))
    max_bytes = normalize_log_file_size_mb(getattr(log_config, 'log_file_size_mb', DEFAULT_LOG_FILE_SIZE_MB)) * 1024 * 1024
    log_path = paths.logs_dir / LOG_FILE_NAME
    with _LOCK:
        logger = logging.getLogger(_LOGGER_NAME)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if _needs_reconfigure(log_path, max_bytes):
            _detach_handler(logger)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(str(log_path), maxBytes=max_bytes, backupCount=LOG_BACKUP_COUNT, encoding='utf-8')
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
            logger.addHandler(handler)
            global _ACTIVE_HANDLER, _ACTIVE_LOG_PATH, _ACTIVE_MAX_BYTES
            _ACTIVE_HANDLER = handler
            _ACTIVE_LOG_PATH = log_path
            _ACTIVE_MAX_BYTES = max_bytes
            try:
                if log_path.exists() and log_path.stat().st_size > max_bytes:
                    handler.doRollover()
            except OSError:
                pass
            _configure_fault_handler(paths.logs_dir / FAULT_LOG_FILE_NAME, max_bytes)
        return logger


def install_exception_logging(paths: DataPaths | None = None, config: AppConfig | None = None) -> logging.Logger:
    resolved_paths = paths
    if resolved_paths is None:
        try:
            resolved_paths = ensure_data_paths()
        except Exception:
            resolved_paths = None
    if resolved_paths is None:
        return logging.getLogger(_LOGGER_NAME)
    resolved_config = config or AppConfig(vault_path='', data_root=str(resolved_paths.global_root))
    logger = configure_file_logging(resolved_paths, resolved_config)
    _install_exception_hooks()
    return logger


def clear_log_files(paths: DataPaths, config: AppConfig | None = None) -> None:
    with _LOCK:
        logger = logging.getLogger(_LOGGER_NAME)
        _detach_handler(logger)
        _close_fault_handler()
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        for item in paths.logs_dir.iterdir():
            try:
                if item.is_dir():
                    for child in item.rglob('*'):
                        if child.is_file():
                            child.unlink(missing_ok=True)
                    for child in sorted(item.rglob('*'), reverse=True):
                        if child.is_dir():
                            child.rmdir()
                    item.rmdir()
                else:
                    item.unlink(missing_ok=True)
            except OSError:
                continue
    configured = configure_file_logging(paths, config)
    configured.info('Log directory was cleared by the user and recreated immediately.')


def shutdown_logging() -> None:
    with _LOCK:
        _detach_handler(logging.getLogger(_LOGGER_NAME))
        _close_fault_handler()


def _needs_reconfigure(log_path: Path, max_bytes: int) -> bool:
    return _ACTIVE_HANDLER is None or _ACTIVE_LOG_PATH != log_path or _ACTIVE_MAX_BYTES != max_bytes


def _detach_handler(logger: logging.Logger) -> None:
    global _ACTIVE_HANDLER, _ACTIVE_LOG_PATH, _ACTIVE_MAX_BYTES
    if _ACTIVE_HANDLER is not None:
        try:
            logger.removeHandler(_ACTIVE_HANDLER)
        except ValueError:
            pass
        try:
            _ACTIVE_HANDLER.flush()
        except Exception:
            pass
        try:
            _ACTIVE_HANDLER.close()
        except Exception:
            pass
    _ACTIVE_HANDLER = None
    _ACTIVE_LOG_PATH = None
    _ACTIVE_MAX_BYTES = 0


def _configure_fault_handler(path: Path, max_bytes: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists() and path.stat().st_size >= max_bytes:
            backup = path.with_suffix(path.suffix + '.1')
            backup.unlink(missing_ok=True)
            path.replace(backup)
    except OSError:
        pass
    _close_fault_handler()
    try:
        handle = path.open('a', encoding='utf-8')
    except OSError:
        return
    global _FAULT_HANDLE
    _FAULT_HANDLE = handle
    try:
        if faulthandler.is_enabled():
            faulthandler.disable()
        faulthandler.enable(file=handle, all_threads=True)
    except Exception:
        try:
            handle.close()
        except Exception:
            pass
        _FAULT_HANDLE = None


def _close_fault_handler() -> None:
    global _FAULT_HANDLE
    try:
        if faulthandler.is_enabled():
            faulthandler.disable()
    except Exception:
        pass
    if _FAULT_HANDLE is not None:
        try:
            _FAULT_HANDLE.flush()
        except Exception:
            pass
        try:
            _FAULT_HANDLE.close()
        except Exception:
            pass
    _FAULT_HANDLE = None


def _install_exception_hooks() -> None:
    global _EXCEPTION_HOOKS_INSTALLED
    if _EXCEPTION_HOOKS_INSTALLED:
        return

    def _sys_hook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            _PREVIOUS_SYS_EXCEPTHOOK(exc_type, exc_value, exc_traceback)
            return
        logging.getLogger(f'{_LOGGER_NAME}.crash').critical('Unhandled exception reached sys.excepthook.', exc_info=(exc_type, exc_value, exc_traceback))
        _PREVIOUS_SYS_EXCEPTHOOK(exc_type, exc_value, exc_traceback)

    def _thread_hook(args):
        if issubclass(args.exc_type, KeyboardInterrupt):
            if callable(_PREVIOUS_THREAD_EXCEPTHOOK):
                _PREVIOUS_THREAD_EXCEPTHOOK(args)
            return
        logging.getLogger(f'{_LOGGER_NAME}.crash').critical(
            'Unhandled exception in thread %s.',
            getattr(args.thread, 'name', 'unknown-thread'),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        if callable(_PREVIOUS_THREAD_EXCEPTHOOK):
            _PREVIOUS_THREAD_EXCEPTHOOK(args)

    sys.excepthook = _sys_hook
    if hasattr(threading, 'excepthook'):
        threading.excepthook = _thread_hook
    atexit.register(shutdown_logging)
    _EXCEPTION_HOOKS_INSTALLED = True
