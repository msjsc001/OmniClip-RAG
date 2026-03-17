from __future__ import annotations

import argparse
import os
import re
import shutil
import site
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST_ROOT = ROOT / 'dist'
VERSION_FILE = ROOT / 'omniclip_rag' / '__init__.py'
STAGING_DIST_ROOT = ROOT / 'build' / 'pyinstaller-dist'
WORK_DIR = ROOT / 'build' / 'pyinstaller-work'
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
_VERSION_PATTERN = re.compile(r"__version__\s*=\s*'([^']+)'")


def _read_app_version() -> str:
    payload = VERSION_FILE.read_text(encoding='utf-8')
    match = _VERSION_PATTERN.search(payload)
    if not match:
        raise RuntimeError(f'Unable to parse app version from {VERSION_FILE}')
    return match.group(1)


APP_VERSION = _read_app_version()


@dataclass(frozen=True)
class BuildTarget:
    exe_basename: str
    spec_path: Path
    output_name: str
    output_dir: Path
    release_zip_path: Path
    support_files: dict[Path, Path]
    protected_runtime_dir: Path | None = None

    @property
    def staging_output_dir(self) -> Path:
        return STAGING_DIST_ROOT / self.exe_basename


GUI_TARGET = BuildTarget(
    exe_basename='OmniClipRAG',
    spec_path=ROOT / 'OmniClipRAG.spec',
    output_name=f'OmniClipRAG-v{APP_VERSION}',
    output_dir=DIST_ROOT / f'OmniClipRAG-v{APP_VERSION}',
    release_zip_path=DIST_ROOT / f'OmniClipRAG-v{APP_VERSION}-win64.zip',
    support_files={
        ROOT / 'scripts' / 'install_runtime.ps1': DIST_ROOT / f'OmniClipRAG-v{APP_VERSION}' / 'InstallRuntime.ps1',
        ROOT / 'RUNTIME_SETUP.md': DIST_ROOT / f'OmniClipRAG-v{APP_VERSION}' / 'RUNTIME_SETUP.md',
    },
    protected_runtime_dir=DIST_ROOT / f'OmniClipRAG-v{APP_VERSION}' / 'runtime',
)
MCP_TARGET = BuildTarget(
    exe_basename='OmniClipRAG-MCP',
    spec_path=ROOT / 'OmniClipRAG-MCP.spec',
    output_name=f'OmniClipRAG-MCP-v{APP_VERSION}',
    output_dir=DIST_ROOT / f'OmniClipRAG-MCP-v{APP_VERSION}',
    release_zip_path=DIST_ROOT / f'OmniClipRAG-MCP-v{APP_VERSION}-win64.zip',
    support_files={
        ROOT / 'MCP_SETUP.md': DIST_ROOT / f'OmniClipRAG-MCP-v{APP_VERSION}' / 'MCP_SETUP.md',
        ROOT / 'examples' / 'mcp' / 'claude_desktop.json': DIST_ROOT / f'OmniClipRAG-MCP-v{APP_VERSION}' / 'examples' / 'mcp' / 'claude_desktop.json',
        ROOT / 'examples' / 'mcp' / 'cursor.json': DIST_ROOT / f'OmniClipRAG-MCP-v{APP_VERSION}' / 'examples' / 'mcp' / 'cursor.json',
        ROOT / 'examples' / 'mcp' / 'cline.json': DIST_ROOT / f'OmniClipRAG-MCP-v{APP_VERSION}' / 'examples' / 'mcp' / 'cline.json',
    },
)
LEGACY_OUTPUT_DIR = DIST_ROOT / 'OmniClipRAG_App'
TARGETS = (GUI_TARGET, MCP_TARGET)


def _normalize(path: Path) -> Path:
    return path.expanduser().resolve()


def _bundle_payload_root(output_dir: Path) -> Path:
    internal_root = output_dir / '_internal'
    return internal_root if internal_root.exists() else output_dir


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
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _clean_output_dir(target: BuildTarget) -> None:
    if not target.output_dir.exists():
        return
    protected = target.protected_runtime_dir
    for child in target.output_dir.iterdir():
        if protected is not None and _normalize(child) == _normalize(protected):
            continue
        _remove_path(child)


def _clean_previous_outputs(clean: bool) -> None:
    if not clean:
        return
    if WORK_DIR.exists():
        _remove_path(WORK_DIR)
    if STAGING_DIST_ROOT.exists():
        _remove_path(STAGING_DIST_ROOT)
    for target in TARGETS:
        _clean_output_dir(target)
    if LEGACY_OUTPUT_DIR.exists():
        _remove_path(LEGACY_OUTPUT_DIR)
    for target in TARGETS:
        if target.release_zip_path.exists():
            _remove_path(target.release_zip_path)


def _run_pyinstaller(target: BuildTarget) -> None:
    from PyInstaller.__main__ import run as pyinstaller_run

    pyinstaller_run(
        [
            '--noconfirm',
            '--clean',
            '--distpath',
            str(STAGING_DIST_ROOT),
            '--workpath',
            str(WORK_DIR),
            str(target.spec_path),
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


def _install_staged_bundle(target: BuildTarget) -> None:
    if not target.staging_output_dir.exists():
        raise RuntimeError(f'Staged build output is missing: {target.staging_output_dir}')
    target.output_dir.mkdir(parents=True, exist_ok=True)
    _copy_tree_contents(target.staging_output_dir, target.output_dir)


def _copy_support_files(target: BuildTarget) -> None:
    target.output_dir.mkdir(parents=True, exist_ok=True)
    for source, destination in target.support_files.items():
        if not source.exists():
            raise RuntimeError(f'Missing release support file: {source}')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _is_forbidden_bundle_package(part: str) -> bool:
    normalized = part.strip().lower().replace('-', '_')
    for prefix in FORBIDDEN_BUNDLE_PACKAGE_PREFIXES:
        expected = prefix.lower().replace('-', '_')
        if normalized == expected:
            return True
        if normalized.startswith(expected + '.') or normalized.startswith(expected + '_') or normalized.startswith(expected + '-'):
            return True
    return False


def _audit_bundle(target: BuildTarget) -> None:
    app_exe = target.output_dir / f'{target.exe_basename}.exe'
    if not app_exe.exists():
        raise RuntimeError(f'Build output is missing {target.exe_basename}.exe: {app_exe}')
    for required in target.support_files.values():
        if not required.exists():
            raise RuntimeError(f'Missing release support file: {required}')
    payload_root = _bundle_payload_root(target.output_dir)
    required_resources = [payload_root / 'resources' / 'tika_suffixes_3.2.3.txt']
    if target.exe_basename == 'OmniClipRAG':
        required_resources.extend(
            (
                payload_root / 'resources' / 'app_icon.ico',
                payload_root / 'resources' / 'app_icon.png',
            )
        )
    for required in required_resources:
        if not required.exists():
            raise RuntimeError(f'Missing required resource: {required}')
    for path in payload_root.rglob('*'):
        relative = path.relative_to(target.output_dir).as_posix().lower()
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
    for target in TARGETS:
        if not target.output_dir.exists():
            continue
        for child in target.output_dir.iterdir():
            if target.protected_runtime_dir is not None and _normalize(child) == _normalize(target.protected_runtime_dir):
                continue
            if child.is_file():
                total += child.stat().st_size
            elif child.is_dir():
                total += _directory_size(child)
    return total


def _build_release_zip(target: BuildTarget) -> None:
    target.output_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target.release_zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(target.output_dir.rglob('*')):
            if path.is_dir():
                continue
            relative = path.relative_to(target.output_dir)
            if relative.parts and relative.parts[0].lower() == 'runtime':
                continue
            archive.write(path, arcname=(Path(target.output_name) / relative).as_posix())


def _summarize() -> None:
    size_mb = _core_bundle_size() / (1024 * 1024)
    for target in TARGETS:
        print(f'Build succeeded: {target.output_dir}')
        print(f'Executable: {target.output_dir / f"{target.exe_basename}.exe"}')
        if target.protected_runtime_dir is not None and target.protected_runtime_dir.exists():
            print(f'Preserved runtime: {target.protected_runtime_dir}')
        print(f'Release zip: {target.release_zip_path}')
    print(f'Approx app bundle size (excluding runtime): {size_mb:.1f} MB')


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Build the lean onedir bundle into a versioned dist/OmniClipRAG-vX.Y.Z folder while preserving any existing local runtime trees.')
    parser.add_argument('--no-clean', action='store_true', help='Keep previous staging/output files if they already exist.')
    parser.add_argument('--skip-audit', action='store_true', help='Skip the post-build purity audit.')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _prepare_import_path()
    _clean_previous_outputs(clean=not args.no_clean)
    for target in TARGETS:
        _run_pyinstaller(target)
        _install_staged_bundle(target)
        _copy_support_files(target)
        if not args.skip_audit:
            _audit_bundle(target)
        _build_release_zip(target)
    _summarize()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
