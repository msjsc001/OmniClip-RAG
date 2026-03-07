param(
    [ValidateSet('onedir', 'onefile')]
    [string]$Mode = 'onedir'
)

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).ProviderPath
$python = Join-Path $root ".venv\Scripts\python.exe"
$packages = Join-Path $root ".packages"
$vendor = Join-Path $root ".vendor"
$target = Join-Path $root "launcher.py"
$resources = Join-Path $root "resources"
$icon = Join-Path $resources "app_icon.ico"

if (-not (Test-Path $python)) {
    throw "找不到虚拟环境 Python：$python"
}

$pythonPathParts = @()
foreach ($path in @($packages, $vendor, $root)) {
    if ($path -and (Test-Path $path)) {
        $pythonPathParts += $path
    }
}

$env:PYTHONNOUSERSITE = "1"
$env:PYTHONPATH = ($pythonPathParts -join ';')
$env:OMNICLIP_ROOT = $root
$env:OMNICLIP_PACKAGES = $packages
$env:OMNICLIP_VENDOR = $vendor
$env:OMNICLIP_TARGET = $target
$env:OMNICLIP_RESOURCES = $resources
$env:OMNICLIP_ICON = $icon
$env:OMNICLIP_BUILD_MODE = $Mode
$code = @"
import os
import sys
import site
from pathlib import Path

os.environ['PYTHONNOUSERSITE'] = '1'
site.getusersitepackages = lambda: ''

try:
    orig_getsitepackages = site.getsitepackages
except AttributeError:
    orig_getsitepackages = None

if orig_getsitepackages is not None:
    def _safe_getsitepackages():
        roots = []
        for path in orig_getsitepackages():
            if 'AppData\\Roaming\\Python' in path:
                continue
            roots.append(path)
        return roots

    site.getsitepackages = _safe_getsitepackages

root = os.environ['OMNICLIP_ROOT']
packages = os.environ.get('OMNICLIP_PACKAGES', '')
vendor = os.environ.get('OMNICLIP_VENDOR', '')
target = os.environ['OMNICLIP_TARGET']
resources = os.environ.get('OMNICLIP_RESOURCES', '')
icon = os.environ.get('OMNICLIP_ICON', '')
mode = os.environ.get('OMNICLIP_BUILD_MODE', 'onedir')

for path in [packages, vendor, root]:
    if path and Path(path).exists() and path not in sys.path:
        sys.path.insert(0, path)

from PyInstaller.__main__ import run

args = [
    '--noconfirm',
    '--clean',
    '--name', 'OmniClipRAG',
    '--hidden-import', 'lancedb',
    '--hidden-import', 'pyarrow',
    '--hidden-import', 'watchdog.events',
    '--hidden-import', 'watchdog.observers',
    '--hidden-import', 'charset_normalizer',
    '--collect-submodules', 'transformers.models',
    '--collect-submodules', 'sentence_transformers',
    '--exclude-module', 'rich',
    '--runtime-hook', str(Path(root) / 'pyi_rth_omniclip.py'),
]
args.append('--onefile' if mode == 'onefile' else '--onedir')
args.append('--windowed')
if resources and Path(resources).exists():
    args.extend(['--add-data', f'{resources};resources'])
if icon and Path(icon).exists():
    args.extend(['--icon', icon])
for path in [root, packages, vendor]:
    if path and Path(path).exists():
        args.extend(['--paths', path])
args.append(target)
run(args)
spec_path = Path(root) / "OmniClipRAG.spec"
if spec_path.exists():
    spec_path.unlink()
"@

$code | & $python -
