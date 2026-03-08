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
    throw "找不到可用的 Python。请先安装 Python 3.13+，然后重新运行 InstallRuntime.ps1。"
}

Write-Host "Installing OmniClip runtime profile '$Profile' into $target"

if ($Profile -eq 'cuda') {
    & $pythonExe @pythonPrefix -m pip install --upgrade --target $target --index-url https://download.pytorch.org/whl/cu128 torch==2.10.0
} else {
    & $pythonExe @pythonPrefix -m pip install --upgrade --target $target --index-url https://download.pytorch.org/whl/cpu torch==2.10.0
}
if ($LASTEXITCODE -ne 0) { throw "PyTorch runtime installation failed." }

& $pythonExe @pythonPrefix -m pip install --upgrade --target $target sentence-transformers==5.2.3 huggingface-hub==0.36.0 safetensors==0.7.0
if ($LASTEXITCODE -ne 0) { throw "SentenceTransformer runtime installation failed." }

Write-Host "Runtime installation completed. Restart OmniClipRAG and retry model bootstrap or indexing."
