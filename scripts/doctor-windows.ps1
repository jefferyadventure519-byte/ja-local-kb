[CmdletBinding()]
param(
    [string]$InstallRoot = 'D:\JA-Local-KB'
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $InstallRoot 'runtime\venv\Scripts\python.exe'
$settings = Join-Path $InstallRoot 'config\settings.json'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Installed Python is missing: $python"
}
if (-not (Test-Path -LiteralPath $settings -PathType Leaf)) {
    throw "Settings are missing: $settings"
}
& $python -m ja_local_kb.cli --settings $settings doctor
exit $LASTEXITCODE
