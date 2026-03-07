$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$python = Join-Path $root ".venv\Scripts\python.exe"
$packages = Join-Path $root ".packages"
$vendor = Join-Path $root ".vendor"

if (-not (Test-Path $python)) {
    throw "找不到虚拟环境 Python：$python"
}

$paths = @($root)
if (Test-Path $packages) { $paths = @($packages) + $paths }
if (Test-Path $vendor) { $paths = @($vendor) + $paths }

$joined = ($paths -join ';')
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$joined;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $joined
}

& $python -m omniclip_rag.gui @args
