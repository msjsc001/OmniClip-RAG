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


# Why: MCP 壳子仍然复用同一套本地检索核心，因此需要共享最小资源面与运行时引导，
# 但继续把重型 AI/runtime 依赖留在外部 runtime/ 中，而不是重新塞进包体。
datas: list[tuple[str, str]] = [
    (str(ROOT / 'resources' / 'tika_suffixes_3.2.3.txt'), 'resources'),
]

binaries: list[tuple[str, str]] = []
hiddenimports = [
    'omniclip_rag.app_entry.mcp',
    'omniclip_rag.headless.bootstrap',
    'omniclip_rag.mcp.core',
    'mcp',
    'mcp.server',
    'mcp.server.stdio',
    'mcp.server.lowlevel',
    'mcp.server.lowlevel.server',
    'mcp.types',
    'multiprocessing',
    '_multiprocessing',
    'asyncio',
    '_asyncio',
    '_overlapped',
    'charset_normalizer.md',
]

for package in (
    'omniclip_rag.headless',
    'omniclip_rag.mcp',
):
    hiddenimports.extend(_safe_collect_hidden(package))

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

for package in (
    'asyncio',
    'concurrent',
    'http',
    'multiprocessing',
):
    hiddenimports.extend(_safe_collect_hidden(package))
hiddenimports.extend(['timeit'])

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
    'PySide6',
    'shiboken6',
]

block_cipher = None


a = Analysis(
    ['launcher_mcp.py'],
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
    name='OmniClipRAG-MCP',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
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
    name='OmniClipRAG-MCP',
)
