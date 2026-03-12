from __future__ import annotations

import argparse
import os
import shutil
import site
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST_ROOT = ROOT / 'dist'
OUTPUT_NAME = 'OmniClipRAG'
OUTPUT_DIR = DIST_ROOT / OUTPUT_NAME
LEGACY_OUTPUT_DIR = DIST_ROOT / 'OmniClipRAG_App'
PROTECTED_RUNTIME_DIR = OUTPUT_DIR / 'runtime'
STAGING_DIST_ROOT = ROOT / 'build' / 'pyinstaller-dist'
STAGING_OUTPUT_DIR = STAGING_DIST_ROOT / OUTPUT_NAME
WORK_DIR = ROOT / 'build' / 'pyinstaller-work'
SPEC_PATH = ROOT / 'OmniClipRAG.spec'
RUNTIME_SUPPORT_FILES = {
    ROOT / 'scripts' / 'install_runtime.ps1': OUTPUT_DIR / 'InstallRuntime.ps1',
    ROOT / 'RUNTIME_SETUP.md': OUTPUT_DIR / 'RUNTIME_SETUP.md',
}
BLACKLIST_FRAGMENTS = (
    'workspaces/',
    'shared/cache/',
    'shared/logs/',
    'local_appdata/',
    'appdata/roaming/omniclip rag/',
    'appdata/local/omniclip rag/',
    'baai__bge-m3',
    'baai__bge-reranker-v2-m3',
    '.lance',
    'omniclip.sqlite3',
)
FORBIDDEN_BUNDLE_PACKAGE_PREFIXES = (
    'torch',
    'transformers',
    'lancedb',
    'pyarrow',
    'scipy',
    'onnxruntime',
    'sentence_transformers',
    'sentence-transformers',
    'numpy',
    'pandas',
)


def _normalize(path: Path) -> Path:
    return path.expanduser().resolve()


def _bundle_payload_root() -> Path:
    internal_root = OUTPUT_DIR / '_internal'
    return internal_root if internal_root.exists() else OUTPUT_DIR


def _prepare_import_path() -> None:
    os.environ['PYTHONNOUSERSITE'] = '1'
    site.ENABLE_USER_SITE = False
    try:
        site.getusersitepackages = lambda: ''
    except Exception:
        pass
    for candidate in (ROOT, ROOT / '.packages', ROOT / '.vendor'):
        if not candidate.exists():
            continue
        candidate_path = str(candidate)
        if candidate_path not in sys.path:
            sys.path.insert(0, candidate_path)


def _remove_path(path: Path) -> None:
    if not path.exists():
        return
    normalized = _normalize(path)
    protected = _normalize(PROTECTED_RUNTIME_DIR)
    if normalized == protected or protected in normalized.parents:
        raise RuntimeError(f'Refusing to remove protected runtime path: {normalized}')
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _clean_output_dir_preserving_runtime() -> None:
    if not OUTPUT_DIR.exists():
        return
    for child in OUTPUT_DIR.iterdir():
        if _normalize(child) == _normalize(PROTECTED_RUNTIME_DIR):
            continue
        _remove_path(child)


def _clean_previous_outputs(clean: bool) -> None:
    if not clean:
        return
    _remove_path(WORK_DIR) if WORK_DIR.exists() else None
    _remove_path(STAGING_DIST_ROOT) if STAGING_DIST_ROOT.exists() else None
    _clean_output_dir_preserving_runtime()
    if LEGACY_OUTPUT_DIR.exists():
        _remove_path(LEGACY_OUTPUT_DIR)


def _run_pyinstaller() -> None:
    from PyInstaller.__main__ import run as pyinstaller_run

    pyinstaller_run(
        [
            '--noconfirm',
            '--clean',
            '--distpath',
            str(STAGING_DIST_ROOT),
            '--workpath',
            str(WORK_DIR),
            str(SPEC_PATH),
        ]
    )


def _copy_tree_contents(source: Path, target: Path) -> None:
    for item in source.iterdir():
        destination = target / item.name
        if destination.exists():
            _remove_path(destination)
        if item.is_dir():
            shutil.copytree(item, destination)
        else:
            shutil.copy2(item, destination)


def _install_staged_bundle() -> None:
    if not STAGING_OUTPUT_DIR.exists():
        raise RuntimeError(f'Staged build output is missing: {STAGING_OUTPUT_DIR}')
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _copy_tree_contents(STAGING_OUTPUT_DIR, OUTPUT_DIR)


def _copy_runtime_support_files() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for source, target in RUNTIME_SUPPORT_FILES.items():
        if not source.exists():
            raise RuntimeError(f'Missing release support file: {source}')
        shutil.copy2(source, target)


def _is_forbidden_bundle_package(part: str) -> bool:
    normalized = part.strip().lower().replace('-', '_')
    for prefix in FORBIDDEN_BUNDLE_PACKAGE_PREFIXES:
        expected = prefix.lower().replace('-', '_')
        if normalized == expected:
            return True
        if normalized.startswith(expected + '.') or normalized.startswith(expected + '_') or normalized.startswith(expected + '-'):
            return True
    return False


def _audit_bundle() -> None:
    launcher_exe = OUTPUT_DIR / 'launcher.exe'
    if not launcher_exe.exists():
        raise RuntimeError(f'Build output is missing launcher.exe: {launcher_exe}')
    for required in (
        OUTPUT_DIR / 'InstallRuntime.ps1',
        OUTPUT_DIR / 'RUNTIME_SETUP.md',
    ):
        if not required.exists():
            raise RuntimeError(f'Missing runtime support file: {required}')
    payload_root = _bundle_payload_root()
    for required in (
        payload_root / 'resources' / 'app_icon.ico',
        payload_root / 'resources' / 'app_icon.png',
    ):
        if not required.exists():
            raise RuntimeError(f'Missing required resource: {required}')
    for path in payload_root.rglob('*'):
        relative = path.relative_to(OUTPUT_DIR).as_posix().lower()
        if any(fragment in relative for fragment in BLACKLIST_FRAGMENTS):
            raise RuntimeError(f'Bundle purity audit failed, forbidden runtime payload detected: {relative}')
        relative_parts = path.relative_to(payload_root).parts
        for part in relative_parts:
            if _is_forbidden_bundle_package(part):
                raise RuntimeError(f'Bundle purity audit failed, forbidden packaged dependency detected: {relative}')


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob('*'):
        if item.is_file():
            total += item.stat().st_size
    return total


def _core_bundle_size() -> int:
    total = 0
    if not OUTPUT_DIR.exists():
        return total
    for child in OUTPUT_DIR.iterdir():
        if _normalize(child) == _normalize(PROTECTED_RUNTIME_DIR):
            continue
        if child.is_file():
            total += child.stat().st_size
        elif child.is_dir():
            total += _directory_size(child)
    return total


def _summarize() -> None:
    size_mb = _core_bundle_size() / (1024 * 1024)
    print(f'Build succeeded: {OUTPUT_DIR}')
    print(f'Launcher: {OUTPUT_DIR / "launcher.exe"}')
    if PROTECTED_RUNTIME_DIR.exists():
        print(f'Preserved runtime: {PROTECTED_RUNTIME_DIR}')
    print(f'Approx app bundle size (excluding runtime): {size_mb:.1f} MB')


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Build the lean onedir bundle into dist/OmniClipRAG while preserving dist/OmniClipRAG/runtime.')
    parser.add_argument('--no-clean', action='store_true', help='Keep previous staging/output files if they already exist.')
    parser.add_argument('--skip-audit', action='store_true', help='Skip the post-build purity audit.')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _prepare_import_path()
    _clean_previous_outputs(clean=not args.no_clean)
    _run_pyinstaller()
    _install_staged_bundle()
    _copy_runtime_support_files()
    if not args.skip_audit:
        _audit_bundle()
    _summarize()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
