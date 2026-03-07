param(
    [string]$Requirements = "requirements.txt",
    [string[]]$Packages,
    [switch]$KeepWheelhouse
)

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$target = Join-Path $root ".packages"
$wheelhouse = Join-Path $root ".wheelhouse"
$temp = Join-Path $root ".tmp"

New-Item -ItemType Directory -Force $temp | Out-Null
if (Test-Path $target) { Remove-Item -Recurse -Force $target }
if (Test-Path $wheelhouse) { Remove-Item -Recurse -Force $wheelhouse }
New-Item -ItemType Directory -Force $target | Out-Null
New-Item -ItemType Directory -Force $wheelhouse | Out-Null

$env:TEMP = $temp
$env:TMP = $temp

if ($Packages -and $Packages.Count -gt 0) {
    python -m pip download --dest $wheelhouse @Packages
} else {
    python -m pip download --dest $wheelhouse -r (Join-Path $root $Requirements)
}

$code = @"
from pathlib import Path
import shutil
import zipfile

wheelhouse = Path(r'$wheelhouse')
target = Path(r'$target')
count = 0
for wheel in wheelhouse.glob('*.whl'):
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(target)
    count += 1
print(f'extracted {count} wheels into {target}')
"@

$code | python -

if (-not $KeepWheelhouse) {
    Remove-Item -Recurse -Force $wheelhouse
}
