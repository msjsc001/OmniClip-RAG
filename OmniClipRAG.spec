# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

_spec_anchor = Path(globals().get('SPECPATH', Path.cwd()))
ROOT = _spec_anchor.resolve().parent if _spec_anchor.is_file() else _spec_anchor.resolve()
ICON = ROOT / 'resources' / 'app_icon.ico'
RUNTIME_HOOK = ROOT / 'pyi_rth_omniclip.py'
HOOKS_DIR = ROOT / 'pyinstaller_hooks'


def _safe_collect_hidden(package: str) -> list[str]:
    try:
        return collect_submodules(package)
    except Exception:
        return []


# Why: 纯净主程序只打包 Qt 壳子和业务代码，所有重型 AI/runtime 统一外置到 runtime/。
datas: list[tuple[str, str]] = [
    (str(ROOT / 'resources' / 'app_icon.ico'), 'resources'),
    (str(ROOT / 'resources' / 'app_icon.png'), 'resources'),
    (str(ROOT / 'resources' / 'app_icon_32.png'), 'resources'),
]

binaries: list[tuple[str, str]] = []
hiddenimports = [
    'omniclip_rag.app_entry.desktop',
    'omniclip_rag.ui_next_qt.app',
    'omniclip_rag.ui_next_qt.main_window',
    'omniclip_rag.ui_next_qt.config_workspace',
    'omniclip_rag.ui_next_qt.query_workspace',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'shiboken6',
    'multiprocessing',
    '_multiprocessing',
    'asyncio',
    '_asyncio',
    '_overlapped',
    'charset_normalizer.md',
]

for package in (
    'omniclip_rag.ui_next_qt',
    'omniclip_rag.ui_shared',
):
    hiddenimports.extend(_safe_collect_hidden(package))

# Why: the packaged shell keeps heavy AI/runtime packages outside the bundle, so
# PyInstaller cannot statically see every stdlib module those runtime wheels
# import later. When CPU semantic search loads torch/transformers at query-time,
# the frozen app still needs a complete-enough stdlib surface (for example pdb,
# timeit, http.cookies, asyncio.base_events, concurrent.futures.process). We
# therefore ship a curated stdlib support set alongside the frozen app instead of
# discovering missing modules one by one at user runtime.
_RUNTIME_STDLIB_PACKAGES = (
    '_pyrepl',
    'asyncio',
    'concurrent',
    'ctypes',
    'email',
    'html',
    'http',
    'importlib.metadata',
    'importlib.resources',
    'multiprocessing',
    'sqlite3',
    'sysconfig',
    'unittest',
    'urllib',
    'zipfile',
    'zoneinfo',
)
_RUNTIME_STDLIB_MODULES = (
    '_compat_pickle',
    'argparse',
    'base64',
    'bdb',
    'bisect',
    'cProfile',
    'calendar',
    'cmd',
    'code',
    'codeop',
    'colorsys',
    'configparser',
    'contextvars',
    'csv',
    'datetime',
    'decimal',
    'difflib',
    'filecmp',
    'fileinput',
    'fractions',
    'getpass',
    'gettext',
    'gzip',
    'hmac',
    'ipaddress',
    'mimetypes',
    'nturl2path',
    'numbers',
    'pdb',
    'pickle',
    'pickletools',
    'pkgutil',
    'platform',
    'pprint',
    'profile',
    'pstats',
    'pydoc',
    'quopri',
    'random',
    'rlcompleter',
    'runpy',
    'secrets',
    'selectors',
    'shlex',
    'socket',
    'ssl',
    'statistics',
    'tarfile',
    'tempfile',
    'timeit',
    'uuid',
)
for package in _RUNTIME_STDLIB_PACKAGES:
    hiddenimports.extend(_safe_collect_hidden(package))
hiddenimports.extend(_RUNTIME_STDLIB_MODULES)

# Keep the legacy minimal declarations too; they are harmless once deduped.
for package in (
    'asyncio',
    'concurrent',
    'http',
    'multiprocessing',
):
    hiddenimports.extend(_safe_collect_hidden(package))
hiddenimports.extend([
    'timeit',
])

hiddenimports = sorted(set(hiddenimports))
excludes = [
    'pytest',
    'pytest_asyncio',
    'IPython',
    'jupyter_client',
    'jupyter_core',
    'notebook',
    'matplotlib.tests',
    'omniclip_rag.ui_legacy_tk',
    'omniclip_rag.ui_legacy_tk.app',
    'omniclip_rag.legacy_single_instance',
    'omniclip_rag.gui',
    'tkinter',
    '_tkinter',
    'torch',
    'transformers',
    'lancedb',
    'pyarrow',
    'scipy',
    'onnxruntime',
    'sentence_transformers',
    'numpy',
    'pandas',
    'PySide6.QtWebEngine',
    'PySide6.QtWebEngineCore',
    'PySide6.Qt3DCore',
    'PySide6.Qt3DExtras',
    'PySide6.Qt3DInput',
    'PySide6.Qt3DLogic',
    'PySide6.Qt3DRender',
    'PySide6.QtQml',
    'PySide6.QtQmlCore',
    'PySide6.QtQmlModels',
    'PySide6.QtQuick',
    'PySide6.QtQuickControls2',
    'PySide6.QtQuickWidgets',
]

block_cipher = None


a = Analysis(
    ['launcher.py'],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(HOOKS_DIR)] if HOOKS_DIR.exists() else [],
    hooksconfig={},
    runtime_hooks=[str(RUNTIME_HOOK)],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OmniClipRAG',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    getattr(a, 'symlinks', []),
    strip=False,
    upx=False,
    upx_exclude=[],
    name='OmniClipRAG',
)
