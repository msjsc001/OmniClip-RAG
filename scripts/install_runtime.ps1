param(
    [ValidateSet('cpu', 'cuda')]
    [string]$Profile = 'cpu'
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Test-Path (Join-Path $scriptDir 'OmniClipRAG.exe')) {
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
    'sentence-transformers==5.2.3',
    'huggingface-hub==0.36.0',
    'safetensors==0.7.0'
)
$cleanupPatterns = @(
    'torch',
    'torch-*dist-info',
    'functorch',
    'functorch-*dist-info',
    'torchgen',
    'torchgen-*dist-info'
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

Write-Host "Runtime installation completed. Restart OmniClipRAG and retry model bootstrap or indexing."
