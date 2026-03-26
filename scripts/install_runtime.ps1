param(
    [ValidateSet('cpu', 'cuda')]
    [string]$Profile = 'cpu',

    [ValidateSet('all', 'semantic-core', 'compute-core', 'model-stack', 'vector-store')]
    [string]$Component = 'all',

    [ValidateSet('official', 'mirror')]
    [string]$Source = 'official',

    [string]$WaitForProcessName = '',

    [switch]$ApplyPendingOnly,

    [string]$DiagnosticsPath = '',

    [string]$ResultPath = ''
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ((Test-Path (Join-Path $scriptDir 'launcher.exe')) -or (Test-Path (Join-Path $scriptDir 'OmniClipRAG.exe'))) {
    $appDir = $scriptDir
} else {
    $appDir = (Resolve-Path (Join-Path $scriptDir '..')).ProviderPath
}

function Get-DefaultDataRoot {
    if (-not [string]::IsNullOrWhiteSpace($env:APPDATA)) {
        return (Join-Path $env:APPDATA 'OmniClip RAG')
    }
    return (Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'OmniClip RAG')
}

function Get-PreferredRuntimeRoot {
    $override = [string]($env:OMNICLIP_RUNTIME_ROOT)
    if (-not [string]::IsNullOrWhiteSpace($override)) {
        $resolvedOverride = Resolve-Path -LiteralPath $override -ErrorAction SilentlyContinue
        if ($null -ne $resolvedOverride) {
            return $resolvedOverride.ProviderPath
        }
        return $override
    }
    $defaultRoot = Get-DefaultDataRoot
    $configPath = Join-Path $defaultRoot 'config.json'
    $dataRoot = $defaultRoot
    if (Test-Path $configPath) {
        try {
            $configPayload = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $configuredRoot = [string]($configPayload.data_root)
            if (-not [string]::IsNullOrWhiteSpace($configuredRoot)) {
                $dataRoot = $configuredRoot
            }
        } catch {
        }
    }
    return (Join-Path (Join-Path $dataRoot 'shared') 'runtime')
}

$target = Get-PreferredRuntimeRoot
$pendingRoot = Join-Path $target '.pending'
$pendingComponentRoot = Join-Path $pendingRoot $Component
$componentsRoot = Join-Path $target 'components'
$installVersion = (Get-Date).ToString('yyyyMMddHHmmssfff')
$componentRegistryPath = Join-Path $target '_runtime_components.json'
New-Item -ItemType Directory -Force -Path $target, $pendingRoot, $componentsRoot | Out-Null

function Normalize-ComponentId {
    param([string]$ComponentName)
    $normalized = ([string]($ComponentName) + '').Trim().ToLowerInvariant()
    switch ($normalized) {
        'compute-core' { return 'semantic-core' }
        'model-stack' { return 'semantic-core' }
        'gpu-acceleration' { return 'semantic-core' }
        default { return $(if ($normalized) { $normalized } else { 'all' }) }
    }
}

$normalizedRequestedComponent = Normalize-ComponentId $Component
$payloadTarget = Join-Path $componentsRoot ("{0}-{1}" -f $normalizedRequestedComponent, $installVersion)

function Get-RunningAppCount {
    param([string]$ProcessName)
    if ([string]::IsNullOrWhiteSpace($ProcessName)) {
        return 0
    }
    return @(
        Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
            Where-Object { $_.Id -ne $PID }
    ).Count
}

function Remove-RuntimeEntry {
    param([string]$EntryPath)
    if (-not (Test-Path $EntryPath)) {
        return
    }
    $item = Get-Item -LiteralPath $EntryPath -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) {
        return
    }
    if ($item.PSIsContainer) {
        Remove-Item -LiteralPath $EntryPath -Recurse -Force -ErrorAction Stop
    } else {
        Remove-Item -LiteralPath $EntryPath -Force -ErrorAction Stop
    }
}

function Merge-RuntimeTree {
    param([string]$SourceRoot, [string]$TargetRoot)
    if (-not (Test-Path $SourceRoot)) {
        return
    }
    New-Item -ItemType Directory -Force -Path $TargetRoot | Out-Null
    foreach ($entry in @(Get-ChildItem -LiteralPath $SourceRoot -Force -ErrorAction SilentlyContinue)) {
        if ($entry.Name -in @('.pending', 'components')) {
            continue
        }
        $targetPath = Join-Path $TargetRoot $entry.Name
        if (Test-Path $targetPath) {
            Remove-RuntimeEntry -EntryPath $targetPath
        }
        Move-Item -LiteralPath $entry.FullName -Destination $targetPath -Force
    }
}

function Remove-ItemIfEmpty {
    param([string]$PathValue)
    if (-not (Test-Path $PathValue)) {
        return
    }
    try {
        if (-not (Get-ChildItem -LiteralPath $PathValue -Force -ErrorAction SilentlyContinue)) {
            Remove-Item -LiteralPath $PathValue -Force -ErrorAction SilentlyContinue
        }
    } catch {
    }
}

function Get-LiveRuntimeComponentRoot {
    param([string]$RuntimeDir, [string]$ComponentName)
    $localComponentsRoot = Join-Path $RuntimeDir 'components'
    return (Join-Path $localComponentsRoot $ComponentName)
}

function Normalize-LegacyComponentRoots {
    param([string]$RuntimeDir)
    $localComponentsRoot = Join-Path $RuntimeDir 'components'
    if (-not (Test-Path $localComponentsRoot)) {
        return @()
    }
    $normalized = New-Object System.Collections.Generic.List[string]
    $componentDirs = @(Get-ChildItem -LiteralPath $localComponentsRoot -Directory -ErrorAction SilentlyContinue | Sort-Object Name)
    foreach ($componentDir in $componentDirs) {
        $componentName = Normalize-ComponentId $componentDir.Name
        $targetRoot = Get-LiveRuntimeComponentRoot -RuntimeDir $RuntimeDir -ComponentName $componentName
        if ($componentDir.FullName -eq $targetRoot) {
            $normalized.Add($componentName)
            continue
        }
        if (Test-Path $targetRoot) {
            Merge-RuntimeTree -SourceRoot $componentDir.FullName -TargetRoot $targetRoot
            Remove-Item -LiteralPath $componentDir.FullName -Recurse -Force -ErrorAction SilentlyContinue
        } else {
            Move-Item -LiteralPath $componentDir.FullName -Destination $targetRoot -Force
        }
        $normalized.Add($componentName)
    }
    return @($normalized | Select-Object -Unique)
}

function Apply-PendingRuntimeUpdates {
    param([string]$RuntimeDir)

    $applied = New-Object System.Collections.Generic.List[string]
    foreach ($merged in @(Normalize-LegacyComponentRoots -RuntimeDir $RuntimeDir)) {
        $applied.Add($merged)
    }

    $localPendingRoot = Join-Path $RuntimeDir '.pending'
    if (-not (Test-Path $localPendingRoot)) {
        return @($applied)
    }

    $localComponentsRoot = Join-Path $RuntimeDir 'components'
    New-Item -ItemType Directory -Force -Path $localComponentsRoot | Out-Null
    $componentDirs = @(Get-ChildItem -LiteralPath $localPendingRoot -Directory -ErrorAction SilentlyContinue | Sort-Object Name)
    foreach ($componentDir in $componentDirs) {
        $localManifestPath = Join-Path $componentDir.FullName 'manifest.json'
        $localPayloadDir = Join-Path $componentDir.FullName 'payload'
        if (-not (Test-Path $localManifestPath) -or -not (Test-Path $localPayloadDir)) {
            continue
        }
        $manifest = Get-Content -LiteralPath $localManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $componentName = Normalize-ComponentId ([string]($manifest.component))
        if ([string]::IsNullOrWhiteSpace($componentName)) {
            $componentName = Normalize-ComponentId $componentDir.Name
        }
        $targetRoot = Get-LiveRuntimeComponentRoot -RuntimeDir $RuntimeDir -ComponentName $componentName
        if (Test-Path $targetRoot) {
            Remove-Item -LiteralPath $targetRoot -Recurse -Force -ErrorAction Stop
        }
        Move-Item -LiteralPath $localPayloadDir -Destination $targetRoot -Force
        $applied.Add($componentName)
        Remove-Item -LiteralPath $componentDir.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path $localPendingRoot) {
        Remove-ItemIfEmpty -PathValue $localPendingRoot
    }
    return @($applied | Select-Object -Unique)
}

function Start-PendingApplyHelper {
    param([string]$ProcessName)

    $pwsh = $null
    try {
        $pwsh = (Get-Command pwsh -ErrorAction Stop).Source
    } catch {
        $pwsh = (Get-Command powershell -ErrorAction Stop).Source
    }
    $args = @('-ExecutionPolicy', 'Bypass', '-NoProfile', '-File', $PSCommandPath, '-ApplyPendingOnly')
    if (-not [string]::IsNullOrWhiteSpace($ProcessName)) {
        $args += @('-WaitForProcessName', $ProcessName)
    }
    Start-Process -FilePath $pwsh -ArgumentList $args -WindowStyle Hidden | Out-Null
}

if ($ApplyPendingOnly) {
    $applyLockPath = Join-Path $pendingRoot '.apply.lock'
    $lockStream = $null
    try {
        try {
            $lockStream = [System.IO.File]::Open($applyLockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        } catch {
            exit 0
        }
        $name = ''
        if ($null -ne $WaitForProcessName) {
            $name = $WaitForProcessName.Trim()
        }
        if ($name) {
            while ((Get-RunningAppCount -ProcessName $name) -gt 0) {
                Start-Sleep -Seconds 1
            }
        }
        $applied = @(Apply-PendingRuntimeUpdates -RuntimeDir $target)
        if ($applied.Count -gt 0) {
            Write-Host ("Applied pending runtime updates: " + ($applied -join ', '))
        }
        exit 0
    } finally {
        if ($null -ne $lockStream) {
            $lockStream.Dispose()
        }
        Remove-Item -LiteralPath $applyLockPath -Force -ErrorAction SilentlyContinue
    }
}

$runningApp = $false
$name = ''
if ($null -ne $WaitForProcessName) {
    $name = $WaitForProcessName.Trim()
}
if ($name) {
    $runningApp = (Get-RunningAppCount -ProcessName $name) -gt 0
    if ($runningApp) {
        Write-Host "Detected running process '$name'. Packages will be downloaded into a pending staging area and activated automatically after OmniClipRAG closes."
    }
}

$effectiveProfile = $Profile
$runtimeSupportDir = Join-Path $appDir 'runtime_support'
$driverPath = Join-Path $runtimeSupportDir 'install_runtime_driver.py'
$manifestPath = Join-Path (Join-Path (Join-Path $runtimeSupportDir 'manifests') $effectiveProfile) ($Component + '.json')
$bundledPythonExe = Join-Path $runtimeSupportDir 'python\tools\python.exe'
$pythonExe = $null
$pythonPrefix = @()
$usingBundledPython = Test-Path $bundledPythonExe
if ($usingBundledPython) {
    $pythonExe = $bundledPythonExe
} elseif (-not ((Test-Path (Join-Path $appDir 'launcher.exe')) -or (Test-Path (Join-Path $appDir 'OmniClipRAG.exe')))) {
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
}
if (-not $pythonExe) {
    throw "Bundled runtime Python is missing or incomplete. Re-download the packaged app, then run InstallRuntime.ps1 again."
}
if (-not (Test-Path $driverPath)) {
    throw "Runtime installer driver is missing: $driverPath"
}
if (-not (Test-Path $manifestPath)) {
    throw "Runtime manifest is missing: $manifestPath"
}

if (Test-Path $payloadTarget) {
    Remove-Item -LiteralPath $payloadTarget -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $payloadTarget | Out-Null

$sharedRoot = Split-Path -Parent $target
$runtimeLogDir = Join-Path (Join-Path $sharedRoot 'logs') 'runtime'
$wheelhouseRoot = Join-Path (Join-Path (Join-Path $target '_downloads') $effectiveProfile) $Component
$requestStamp = (Get-Date).ToString('yyyyMMdd-HHmmssfff')
$diagnosticsPath = if ([string]::IsNullOrWhiteSpace($DiagnosticsPath)) { Join-Path $runtimeLogDir ("runtime-install-{0}.json" -f $requestStamp) } else { $DiagnosticsPath }
$resultPath = if ([string]::IsNullOrWhiteSpace($ResultPath)) { Join-Path $runtimeLogDir ("runtime-install-{0}.result.json" -f $requestStamp) } else { $ResultPath }
New-Item -ItemType Directory -Force -Path $runtimeLogDir, $wheelhouseRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $diagnosticsPath), (Split-Path -Parent $resultPath) | Out-Null

Write-Host "Installing OmniClip runtime profile '$effectiveProfile' / component '$Component' / source '$Source' into isolated component root: $payloadTarget"
if ($usingBundledPython) {
    Write-Host "Using bundled Python runtime installer: $pythonExe"
} else {
    Write-Host "Using developer fallback Python interpreter: $pythonExe"
}
Write-Host "Runtime wheelhouse: $wheelhouseRoot"
Write-Host "Runtime diagnostics: $diagnosticsPath"

& $pythonExe @pythonPrefix $driverPath --manifest $manifestPath --profile $effectiveProfile --component $Component --source $Source --runtime-root $target --payload-target $payloadTarget --wheelhouse $wheelhouseRoot --diagnostics-path $diagnosticsPath --result-path $resultPath
if ($LASTEXITCODE -ne 0) {
    $failureDetail = ''
    if (Test-Path $resultPath) {
        try {
            $failurePayload = Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $failureDetail = [string]($failurePayload.error_message)
        } catch {
        }
    }
    try {
        if (Test-Path $payloadTarget) {
            Remove-Item -LiteralPath $payloadTarget -Recurse -Force -ErrorAction SilentlyContinue
        }
    } catch {
    }
    if ([string]::IsNullOrWhiteSpace($failureDetail)) {
        throw "Runtime installation failed. Diagnostic log: $diagnosticsPath"
    }
    throw "Runtime installation failed. $failureDetail Diagnostic log: $diagnosticsPath"
}

$resultPayload = Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json
$cleanupPatterns = @($resultPayload.cleanup_patterns | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
$requiredModules = @($resultPayload.required_modules | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
$effectiveSource = [string]($resultPayload.selected_source)
if ([string]::IsNullOrWhiteSpace($effectiveSource)) {
    $effectiveSource = $Source
}

$registryTempPath = Join-Path $payloadTarget '_registry_tmp.json'
@"
import json
import sys
from pathlib import Path

registry_path = Path(sys.argv[1]).resolve()
component_names = [name.strip() for name in sys.argv[2].split(',') if name.strip()]
runtime_root = Path(sys.argv[3]).resolve()
payload_target_path = Path(sys.argv[4]).resolve()
profile_name = sys.argv[5].strip()
source_name = sys.argv[6].strip()
created_at = sys.argv[7].strip()
registry = {}
if registry_path.exists():
    try:
        payload = json.loads(registry_path.read_text(encoding='utf-8'))
        if isinstance(payload, dict):
            registry = payload
    except Exception:
        registry = {}
if 'all' in registry and 'all' not in component_names:
    registry.pop('all', None)
try:
    payload_target = str(payload_target_path.relative_to(runtime_root))
except Exception:
    payload_target = str(payload_target_path)
for component_name in component_names:
    registry[component_name] = {
        'path': payload_target,
        'profile': profile_name,
        'source': source_name,
        'created_at': created_at,
        'validated': True,
    }
registry_path.write_text(json.dumps(registry, ensure_ascii=True, indent=2), encoding='utf-8')
"@ | & $pythonExe @pythonPrefix -I - $componentRegistryPath $(if ($normalizedRequestedComponent -eq 'all') { 'semantic-core,vector-store' } else { $normalizedRequestedComponent }) $target $payloadTarget $effectiveProfile $effectiveSource (Get-Date).ToString('o')
if ($LASTEXITCODE -ne 0) { throw "Runtime registry update failed." }

Write-Host "Runtime validation succeeded."
Write-Host "Runtime component was installed successfully."
Write-Host "Runtime diagnostic log: $diagnosticsPath"
if ($runningApp) {
    Write-Host "Restart OmniClipRAG.exe after the download finishes. The new runtime component has been registered and the next launch will use it automatically."
} else {
    Write-Host "Runtime component is ready to use immediately."
}
