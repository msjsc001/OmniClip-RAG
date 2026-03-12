param(
    [switch]$NoClean,
    [switch]$SkipAudit
)

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).ProviderPath
$python = Join-Path $root '.venv\Scripts\python.exe'
$buildScript = Join-Path $root 'build.py'

if (-not (Test-Path $python)) {
    throw "找不到虚拟环境 Python：$python"
}
if (-not (Test-Path $buildScript)) {
    throw "找不到构建脚本：$buildScript"
}

$args = @($buildScript)
if ($NoClean) { $args += '--no-clean' }
if ($SkipAudit) { $args += '--skip-audit' }

& $python @args
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
