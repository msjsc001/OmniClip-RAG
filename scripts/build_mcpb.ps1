param(
    [string]$Version = '',
    [switch]$NoClean
)

$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).ProviderPath
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
$python = if (Test-Path $venvPython) { $venvPython } else { 'python' }
$npxCommand = Get-Command 'npx.cmd' -ErrorAction SilentlyContinue
if (-not $npxCommand) {
    $npxCommand = Get-Command 'npx' -ErrorAction Stop
}
$npx = $npxCommand.Source

if (-not $Version) {
    $Version = (& $python -c "from omniclip_rag import __version__; print(__version__)").Trim()
}

$mcpDist = Join-Path $root "dist\OmniClipRAG-MCP-v$Version"
if (-not (Test-Path $mcpDist)) {
    throw "Missing built MCP directory: $mcpDist. Run python build.py or .\\scripts\\build_exe.ps1 first."
}

$iconSource = Join-Path $root 'resources\app_icon.png'
if (-not (Test-Path $iconSource)) {
    throw "Missing MCPB icon resource: $iconSource"
}

$stageRoot = Join-Path $root "build\mcpb\v$Version"
$bundleRoot = Join-Path $stageRoot 'bundle'
$verifyRoot = Join-Path $stageRoot 'verify'
$artifactName = (& $python -c "from omniclip_rag.mcp.registry import mcpb_filename; print(mcpb_filename('$Version'))").Trim()
$artifactPath = Join-Path $root ('dist\' + $artifactName)
$shaPath = $artifactPath + '.sha256'
$summaryPath = Join-Path $stageRoot 'registry-release-summary.json'
$manifestPath = Join-Path $bundleRoot 'manifest.json'
$iconTarget = Join-Path $bundleRoot 'icon.png'
$serverTarget = Join-Path $bundleRoot 'server'
$serverJsonPath = Join-Path $root 'server.json'

if (-not $NoClean) {
    if (Test-Path $stageRoot) {
        Remove-Item -Recurse -Force $stageRoot
    }
    foreach ($path in @($artifactPath, $shaPath)) {
        if (Test-Path $path) {
            Remove-Item -Force $path
        }
    }
}

New-Item -ItemType Directory -Force -Path $bundleRoot | Out-Null
Copy-Item -Path $mcpDist -Destination $serverTarget -Recurse -Force
Copy-Item -Path $iconSource -Destination $iconTarget -Force

$env:OMNICLIP_REGISTRY_VERSION = $Version
$env:OMNICLIP_MANIFEST_PATH = $manifestPath
@'
import json
import os
from pathlib import Path

from omniclip_rag.mcp.registry import build_mcpb_manifest

target = Path(os.environ['OMNICLIP_MANIFEST_PATH'])
payload = build_mcpb_manifest(os.environ['OMNICLIP_REGISTRY_VERSION'])
target.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
'@ | & $python -

& $npx --yes @anthropic-ai/mcpb validate $manifestPath
if ($LASTEXITCODE -ne 0) {
    throw "MCPB manifest validation failed: $manifestPath"
}

& $npx --yes @anthropic-ai/mcpb pack $bundleRoot $artifactPath
if ($LASTEXITCODE -ne 0) {
    throw "MCPB packaging failed: $artifactPath"
}

$hash = (Get-FileHash -Path $artifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -Path $shaPath -Value ($hash + '  ' + [IO.Path]::GetFileName($artifactPath)) -Encoding ascii

$env:OMNICLIP_SERVER_JSON_PATH = $serverJsonPath
$env:OMNICLIP_SERVER_SHA256 = $hash
@'
import json
import os
from pathlib import Path

from omniclip_rag.mcp.registry import build_registry_server_payload

target = Path(os.environ['OMNICLIP_SERVER_JSON_PATH'])
payload = build_registry_server_payload(
    file_sha256=os.environ['OMNICLIP_SERVER_SHA256'],
    version=os.environ['OMNICLIP_REGISTRY_VERSION'],
)
target.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
'@ | & $python -

if (Test-Path $verifyRoot) {
    Remove-Item -Recurse -Force $verifyRoot
}
New-Item -ItemType Directory -Force -Path $verifyRoot | Out-Null
& $npx --yes @anthropic-ai/mcpb unpack $artifactPath $verifyRoot
if ($LASTEXITCODE -ne 0) {
    throw "MCPB unpack verification failed: $artifactPath"
}

$unpackedManifest = Join-Path $verifyRoot 'manifest.json'
if (-not (Test-Path $unpackedManifest)) {
    throw "Missing manifest.json after unpack: $unpackedManifest"
}

$manifest = Get-Content -Path $unpackedManifest -Raw | ConvertFrom-Json
$entryPoint = [string]$manifest.server.entry_point
if (-not $entryPoint) {
    throw "Unpacked manifest is missing server.entry_point."
}
$entryPath = Join-Path $verifyRoot ($entryPoint -replace '/', '\')
if (-not (Test-Path $entryPath)) {
    throw "Manifest entry executable not found: $entryPath"
}
if ([IO.Path]::GetFileName($entryPath) -ne 'OmniClipRAG-MCP.exe') {
    throw "Manifest entry is not OmniClipRAG-MCP.exe: $entryPath"
}

$summary = @{
    version = $Version
    mcpb = [IO.Path]::GetFullPath($artifactPath)
    sha256 = $hash
    server_json = [IO.Path]::GetFullPath($serverJsonPath)
    release_url = (& $python -c "from omniclip_rag.mcp.registry import mcpb_download_url; print(mcpb_download_url('$Version'))").Trim()
    manifest_entry_point = $entryPoint
}
$summary | ConvertTo-Json -Depth 4 | Set-Content -Path $summaryPath -Encoding utf8

Write-Host "MCPB build completed:" $artifactPath
Write-Host "SHA256:" $hash
Write-Host "Registry metadata:" $serverJsonPath
Write-Host "Release summary:" $summaryPath
