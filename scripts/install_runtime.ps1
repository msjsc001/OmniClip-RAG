param(
    [ValidateSet('cpu', 'cuda')]
    [string]$Profile = 'cpu'
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ((Test-Path (Join-Path $scriptDir 'launcher.exe')) -or (Test-Path (Join-Path $scriptDir 'OmniClipRAG.exe'))) {
    $appDir = $scriptDir
} else {
    $appDir = (Resolve-Path (Join-Path $scriptDir '..')).ProviderPath
}
$target = Join-Path $appDir 'runtime'
New-Item -ItemType Directory -Force -Path $target | Out-Null

$pythonExe = $null
$pythonPrefix = @()
try {
    $cmd = Get-Command py -ErrorAction Stop
    $pythonExe = $cmd.Source
    $pythonPrefix = @('-3.13')
} catch {
    try {
        $cmd = Get-Command python -ErrorAction Stop
        $pythonExe = $cmd.Source
        $pythonPrefix = @()
    } catch {
    }
}
if (-not $pythonExe) {
    throw "Python 3.13+ was not found. Install Python first, then run InstallRuntime.ps1 again."
}

$torchIndex = if ($Profile -eq 'cuda') { 'https://download.pytorch.org/whl/cu128' } else { 'https://download.pytorch.org/whl/cpu' }
$packageList = @(
    'torch==2.10.0',
    'sentence-transformers>=5.1.0,<6.0.0',
    'transformers>=4.41.0,<5.0.0',
    'huggingface-hub>=0.20.0,<1.0.0',
    'safetensors>=0.4.0,<1.0.0',
    'lancedb>=0.23.0,<0.30.0',
    'onnxruntime>=1.22.0,<1.25.0',
    'pyarrow>=18.0.0,<21.0.0',
    'numpy>=1.26.0,<3.0.0',
    'scipy>=1.13.0,<2.0.0',
    'pandas>=2.2.0,<3.0.0'
)
$cleanupPatterns = @(
    'torch', 'torch-*dist-info',
    'functorch', 'functorch-*dist-info',
    'torchgen', 'torchgen-*dist-info',
    'sentence_transformers', 'sentence_transformers-*dist-info',
    'transformers', 'transformers-*dist-info',
    'huggingface_hub', 'huggingface_hub-*dist-info',
    'safetensors', 'safetensors-*dist-info',
    'lancedb', 'lancedb-*dist-info',
    'pyarrow', 'pyarrow-*dist-info', 'pyarrow.libs',
    'numpy', 'numpy-*dist-info', 'numpy.libs',
    'pandas', 'pandas-*dist-info',
    'scipy', 'scipy-*dist-info', 'scipy.libs',
    'onnxruntime', 'onnxruntime-*dist-info'
)

Write-Host "Installing OmniClip runtime profile '$Profile' into $target"

foreach ($pattern in $cleanupPatterns) {
    Get-ChildItem -Path $target -Filter $pattern -Force -ErrorAction SilentlyContinue | ForEach-Object {
        Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
}

& $pythonExe @pythonPrefix -m pip install --upgrade --upgrade-strategy only-if-needed --target $target --index-url $torchIndex --extra-index-url https://pypi.org/simple @packageList
if ($LASTEXITCODE -ne 0) { throw "Runtime installation failed." }

$bootstrapPath = Join-Path $target '_runtime_bootstrap.json'
@'
import json
import sys
import sysconfig
from pathlib import Path

target = Path(sys.argv[1])
payload = {
    'python_exe': sys.executable,
    'python_version': sys.version.split()[0],
    'stdlib': sysconfig.get_path('stdlib') or '',
    'platstdlib': sysconfig.get_path('platstdlib') or '',
    'dll_dir': str(Path(sys.base_prefix) / 'DLLs'),
}
target.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding='utf-8')
'@ | & $pythonExe @pythonPrefix - $bootstrapPath
if ($LASTEXITCODE -ne 0) { throw "Runtime bootstrap metadata generation failed." }

@'
import importlib
import json
import os
import sys
from pathlib import Path

runtime_dir = Path(sys.argv[1]).resolve()
metadata = runtime_dir / '_runtime_bootstrap.json'
if metadata.exists():
    payload = json.loads(metadata.read_text(encoding='utf-8'))
    dll_dir = str(payload.get('dll_dir') or '').strip()
else:
    dll_dir = ''

sys.path.insert(0, str(runtime_dir))
existing_paths = []
for candidate in (
    runtime_dir,
    runtime_dir / 'bin',
    runtime_dir / 'pyarrow.libs',
    runtime_dir / 'numpy.libs',
    runtime_dir / 'scipy.libs',
    runtime_dir / 'torch' / 'lib',
    Path(dll_dir) if dll_dir else None,
):
    if candidate is None or not candidate.exists():
        continue
    existing_paths.append(str(candidate))
    if hasattr(os, 'add_dll_directory'):
        try:
            os.add_dll_directory(str(candidate))
        except OSError:
            pass
if existing_paths:
    os.environ['PATH'] = os.pathsep.join(existing_paths + [os.environ.get('PATH', '')])

required = [
    'torch',
    'sentence_transformers',
    'transformers',
    'huggingface_hub',
    'safetensors',
    'lancedb',
    'onnxruntime',
    'pyarrow',
    'numpy',
    'pandas',
    'scipy',
]
failures = []
for module_name in required:
    try:
        importlib.import_module(module_name)
    except Exception as exc:
        failures.append(f'{module_name}: {type(exc).__name__}: {exc}')
if failures:
    raise SystemExit('Runtime validation failed:\n' + '\n'.join(failures))
print('Runtime validation succeeded.')
'@ | & $pythonExe @pythonPrefix - $target
if ($LASTEXITCODE -ne 0) { throw "Runtime validation failed after installation." }

Write-Host "Runtime installation completed. Restart launcher.exe and retry model bootstrap or indexing."
