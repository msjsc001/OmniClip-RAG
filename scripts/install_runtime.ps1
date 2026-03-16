param(
    [ValidateSet('cpu', 'cuda')]
    [string]$Profile = 'cpu',

    [ValidateSet('all', 'semantic-core', 'compute-core', 'model-stack', 'vector-store')]
    [string]$Component = 'all',

    [ValidateSet('official', 'mirror')]
    [string]$Source = 'official',

    [string]$WaitForProcessName = '',

    [switch]$ApplyPendingOnly
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ((Test-Path (Join-Path $scriptDir 'launcher.exe')) -or (Test-Path (Join-Path $scriptDir 'OmniClipRAG.exe'))) {
    $appDir = $scriptDir
} else {
    $appDir = (Resolve-Path (Join-Path $scriptDir '..')).ProviderPath
}
$target = Join-Path $appDir 'runtime'
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

function Clear-RuntimePatterns {
    param([string]$RuntimeDir, [string[]]$Patterns)
    foreach ($pattern in @($Patterns | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })) {
        foreach ($entry in @(Get-ChildItem -LiteralPath $RuntimeDir -Force -ErrorAction SilentlyContinue -Filter $pattern)) {
            if ($entry.Name -in @('.pending', 'components')) {
                continue
            }
            try {
                Remove-RuntimeEntry -EntryPath $entry.FullName
            } catch {
            }
        }
    }
}

function Get-LiveRuntimeComponentRoot {
    param([string]$RuntimeDir, [string]$ComponentName)
    $componentsRoot = Join-Path $RuntimeDir 'components'
    return (Join-Path $componentsRoot $ComponentName)
}

function Normalize-LegacyComponentRoots {
    param([string]$RuntimeDir)
    $componentsRoot = Join-Path $RuntimeDir 'components'
    if (-not (Test-Path $componentsRoot)) {
        return @()
    }
    $normalized = New-Object System.Collections.Generic.List[string]
    $componentDirs = @(Get-ChildItem -LiteralPath $componentsRoot -Directory -ErrorAction SilentlyContinue | Sort-Object Name)
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

    $componentsRoot = Join-Path $RuntimeDir 'components'
    New-Item -ItemType Directory -Force -Path $componentsRoot | Out-Null
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
        try {
            if (-not (Get-ChildItem -LiteralPath $localPendingRoot -Force -ErrorAction SilentlyContinue)) {
                Remove-Item -LiteralPath $localPendingRoot -Force -ErrorAction SilentlyContinue
            }
        } catch {
        }
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

$effectiveProfile = if ($normalizedRequestedComponent -eq 'semantic-core') { 'cpu' } else { $Profile }
$torchIndex = if ($effectiveProfile -eq 'cuda') { 'https://download.pytorch.org/whl/cu128' } else { 'https://download.pytorch.org/whl/cpu' }
$pypiSource = if ($Source -eq 'mirror') { 'https://pypi.tuna.tsinghua.edu.cn/simple' } else { 'https://pypi.org/simple' }

$packageGroups = @{
    'compute-core' = @(
        'torch==2.10.0',
        'numpy>=1.26.0,<3.0.0',
        'scipy>=1.13.0,<2.0.0'
    )
    'model-stack' = @(
        'sentence-transformers>=5.1.0,<6.0.0',
        'transformers>=4.41.0,<5.0.0',
        'huggingface-hub>=0.20.0,<1.0.0',
        'safetensors>=0.4.0,<1.0.0'
    )
    'vector-store' = @(
        'lancedb>=0.23.0,<0.30.0',
        'onnxruntime>=1.22.0,<1.25.0',
        'pyarrow>=18.0.0,<21.0.0',
        'pandas>=2.2.0,<3.0.0'
    )
}
$cleanupGroups = @{
    'compute-core' = @(
        'torch', 'torch-*dist-info',
        'functorch', 'functorch-*dist-info',
        'torchgen', 'torchgen-*dist-info',
        'numpy', 'numpy-*dist-info', 'numpy.libs',
        'scipy', 'scipy-*dist-info', 'scipy.libs'
    )
    'model-stack' = @(
        'sentence_transformers', 'sentence_transformers-*dist-info',
        'transformers', 'transformers-*dist-info',
        'huggingface_hub', 'huggingface_hub-*dist-info',
        'safetensors', 'safetensors-*dist-info'
    )
    'vector-store' = @(
        'lancedb', 'lancedb-*dist-info',
        'onnxruntime', 'onnxruntime-*dist-info',
        'pyarrow', 'pyarrow-*dist-info', 'pyarrow.libs',
        'pandas', 'pandas-*dist-info'
    )
}
$validationGroups = @{
    'compute-core' = @(
        'torch',
        'numpy',
        'scipy'
    )
    'model-stack' = @(
        'sentence_transformers',
        'transformers',
        'huggingface_hub',
        'safetensors'
    )
    'vector-store' = @(
        'lancedb',
        'onnxruntime',
        'pyarrow',
        'pandas'
    )
}

$selectedGroups = switch ($Component) {
    'all' { @('compute-core', 'model-stack', 'vector-store'); break }
    'semantic-core' { @('compute-core', 'model-stack'); break }
    default { @($Component) }
}
$packageList = @()
$cleanupPatterns = @()
$requiredModules = @()
foreach ($group in $selectedGroups) {
    $packageList += $packageGroups[$group]
    $cleanupPatterns += $cleanupGroups[$group]
    $requiredModules += $validationGroups[$group]
}
$packageList = $packageList | Select-Object -Unique
$cleanupPatterns = $cleanupPatterns | Select-Object -Unique
$requiredModules = $requiredModules | Select-Object -Unique

if (Test-Path $payloadTarget) {
    Remove-Item $payloadTarget -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $payloadTarget | Out-Null

Write-Host "Installing OmniClip runtime profile '$effectiveProfile' / component '$Component' / source '$Source' into isolated component root: $payloadTarget"

& $pythonExe @pythonPrefix -m pip install --upgrade --force-reinstall --ignore-installed --target $payloadTarget --index-url $torchIndex --extra-index-url $pypiSource @packageList
if ($LASTEXITCODE -ne 0) { throw "Runtime installation failed." }

$bootstrapPath = Join-Path $payloadTarget '_runtime_bootstrap.json'
@"
import json
import sys
import sysconfig
from pathlib import Path

target = Path(sys.argv[1])
payload = {
    'python_exe': sys.executable,
    'python_version': sys.version.split()[0],
    'stdlib': '',
    'platstdlib': '',
    'dll_dir': '',
}
target.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding='utf-8')
"@ | & $pythonExe @pythonPrefix - $bootstrapPath
if ($LASTEXITCODE -ne 0) { throw "Runtime bootstrap metadata generation failed." }

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$requiredModulesPath = Join-Path $payloadTarget 'required-modules.txt'
[System.IO.File]::WriteAllText($requiredModulesPath, (($requiredModules | Where-Object { $_ }) -join [Environment]::NewLine), $utf8NoBom)
@"
import importlib
import json
import os
import sys
from pathlib import Path

runtime_dir = Path(sys.argv[1]).resolve()
required_modules_path = Path(sys.argv[2]).resolve()
required = [line.strip() for line in required_modules_path.read_text(encoding='utf-8').splitlines() if line.strip()]
metadata = runtime_dir / '_runtime_bootstrap.json'
if metadata.exists():
    payload = json.loads(metadata.read_text(encoding='utf-8'))
    dll_dir = str(payload.get('dll_dir') or '').strip()
else:
    dll_dir = ''

PROBES = {
    'torch': {'import_name': 'torch', 'required_entries': ['__init__.py']},
    'numpy': {'import_name': 'numpy', 'required_entries': ['__init__.py', '_core'], 'attribute': '__version__', 'submodules': ['numpy.core.multiarray']},
    'scipy': {'import_name': 'scipy', 'required_entries': ['__init__.py', 'linalg']},
    'sentence_transformers': {'import_name': 'sentence_transformers', 'required_entries': ['__init__.py'], 'attribute': 'SentenceTransformer'},
    'transformers': {'import_name': 'transformers', 'required_entries': ['__init__.py', 'utils'], 'submodules': ['transformers.utils']},
    'huggingface_hub': {'import_name': 'huggingface_hub', 'required_entries': ['__init__.py', 'hf_api.py'], 'submodules': ['huggingface_hub.hf_api']},
    'safetensors': {'import_name': 'safetensors', 'required_entries': ['__init__.py']},
    'lancedb': {'import_name': 'lancedb', 'required_entries': ['__init__.py']},
    'onnxruntime': {'import_name': 'onnxruntime', 'required_entries': ['__init__.py']},
    'pyarrow': {'import_name': 'pyarrow', 'required_entries': ['__init__.py'], 'submodules': ['pyarrow.lib']},
    'pandas': {'import_name': 'pandas', 'required_entries': ['__init__.py'], 'attribute': '__version__'},
}

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

runtime_root = runtime_dir.resolve()

def is_under_runtime(path_value: str) -> bool:
    try:
        Path(path_value).resolve().relative_to(runtime_root)
        return True
    except Exception:
        return False


def collect_origins(module) -> list[str]:
    origins = []
    module_file = getattr(module, '__file__', None)
    if module_file:
        origins.append(str(Path(module_file).resolve()))
    module_path = getattr(module, '__path__', None)
    if module_path:
        for entry in module_path:
            origins.append(str(Path(entry).resolve()))
    unique = []
    for item in origins:
        if item not in unique:
            unique.append(item)
    return unique

failures = []
for module_name in required:
    probe = PROBES.get(module_name, {'import_name': module_name, 'required_entries': [], 'submodules': []})
    package_root = runtime_dir / module_name
    for required_entry in probe.get('required_entries', []):
        if not (package_root / required_entry).exists():
            failures.append(f"{module_name}: staged files are incomplete (missing {required_entry})")
            break
    else:
        try:
            module = importlib.import_module(probe['import_name'])
            origins = collect_origins(module)
            if not getattr(module, '__file__', None):
                raise ImportError('imported as a namespace package or without __file__')
            if not origins or not all(is_under_runtime(origin) for origin in origins):
                raise ImportError('module resolved outside staged runtime payload')
            required_attr = probe.get('attribute')
            if required_attr and not getattr(module, required_attr, None):
                raise ImportError(f'missing required attribute {required_attr}')
            for submodule_name in probe.get('submodules', []):
                submodule = importlib.import_module(submodule_name)
                submodule_origins = collect_origins(submodule)
                if not submodule_origins or not all(is_under_runtime(origin) for origin in submodule_origins):
                    raise ImportError(f'{submodule_name} resolved outside staged runtime payload')
        except Exception as exc:
            failures.append(f'{module_name}: {type(exc).__name__}: {exc}')
if failures:
    raise SystemExit("Runtime validation failed:\n" + "\n".join(failures))
print('Runtime validation succeeded.')
"@ | & $pythonExe @pythonPrefix -I - $payloadTarget $requiredModulesPath
if ($LASTEXITCODE -ne 0) { throw "Runtime validation failed after installation." }

$validationManifestPath = Join-Path $payloadTarget '_runtime_validation.json'
@"
import json
import sys
from pathlib import Path

target = Path(sys.argv[1]).resolve()
payload = {
    'validated': True,
    'validated_at': sys.argv[2],
}
target.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding='utf-8')
"@ | & $pythonExe @pythonPrefix -I - $validationManifestPath (Get-Date).ToString('o')
if ($LASTEXITCODE -ne 0) { throw "Runtime validation manifest generation failed." }

$registryTempPath = Join-Path $payloadTarget '_registry_tmp.json'
@"
import json
import sys
from pathlib import Path

registry_path = Path(sys.argv[1]).resolve()
component_name = sys.argv[2].strip()
payload_target = str(Path(sys.argv[3]).resolve())
profile_name = sys.argv[4].strip()
source_name = sys.argv[5].strip()
created_at = sys.argv[6].strip()
registry = {}
if registry_path.exists():
    try:
        payload = json.loads(registry_path.read_text(encoding='utf-8'))
        if isinstance(payload, dict):
            registry = payload
    except Exception:
        registry = {}
registry[component_name] = {
    'path': payload_target,
    'profile': profile_name,
    'source': source_name,
    'created_at': created_at,
    'validated': True,
}
registry_path.write_text(json.dumps(registry, ensure_ascii=True, indent=2), encoding='utf-8')
"@ | & $pythonExe @pythonPrefix -I - $componentRegistryPath $normalizedRequestedComponent $payloadTarget $effectiveProfile $Source (Get-Date).ToString('o')
if ($LASTEXITCODE -ne 0) { throw "Runtime registry update failed." }

Write-Host "Runtime validation succeeded."
Write-Host "Runtime component was installed successfully."
if ($runningApp) {
    Write-Host "Restart OmniClipRAG.exe after the download finishes. The new runtime component has been registered and the next launch will use it automatically."
} else {
    Write-Host "Runtime component is ready to use immediately."
}
