[CmdletBinding()]
param(
    [string]$InstallRoot = 'D:\JA-Local-KB',
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [Parameter(Mandatory = $true)]
    [string]$VaultRoot,
    [switch]$ConfirmRollback
)

$ErrorActionPreference = 'Stop'
if (-not $ConfirmRollback) {
    throw 'Rollback requires -ConfirmRollback.'
}
$releaseRoot = Join-Path $InstallRoot "releases\$Version"
if (-not (Test-Path -LiteralPath (Join-Path $releaseRoot 'pyproject.toml'))) {
    throw "Release is unavailable: $Version"
}

$currentPath = Join-Path $InstallRoot 'app\current.json'
$configPath = Join-Path $InstallRoot 'config'
$python = Join-Path $InstallRoot 'runtime\venv\Scripts\python.exe'
$configLifecycle = Join-Path $PSScriptRoot 'config-lifecycle.py'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Installed Python is unavailable: $python"
}
if (-not (Test-Path -LiteralPath $configLifecycle)) {
    throw "Config lifecycle helper is unavailable: $configLifecycle"
}

function Invoke-ConfigLifecycle {
    param([string[]]$Arguments)
    $json = & $python $configLifecycle @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Config lifecycle operation failed: $($Arguments[0])"
    }
    return $json | ConvertFrom-Json
}

$sourceState = Invoke-ConfigLifecycle -Arguments @(
    'inspect',
    '--config-root', $configPath
)
$sourceHash = [string]$sourceState.registry.sha256
$targetConfig = Join-Path $InstallRoot "backups\versions\$Version\config"
if (Test-Path -LiteralPath $targetConfig) {
    Invoke-ConfigLifecycle -Arguments @(
        'validate',
        '--config-root', $configPath,
        '--snapshot-config', $targetConfig
    ) | Out-Null
}
if (
    (Test-Path -LiteralPath $currentPath) -and
    (Test-Path -LiteralPath $configPath)
) {
    $current = Get-Content -Raw -Encoding utf8 -LiteralPath $currentPath |
        ConvertFrom-Json
    $currentVersion = [string]$current.version
    if ($currentVersion -notmatch '^[A-Za-z0-9._-]+$') {
        throw "Unsafe installed version identifier: $currentVersion"
    }
    $snapshotRoot = Join-Path $InstallRoot "backups\versions\$currentVersion"
    $snapshotConfig = Join-Path $snapshotRoot 'config'
    if (-not (Test-Path -LiteralPath $snapshotConfig)) {
        Invoke-ConfigLifecycle -Arguments @(
            'snapshot',
            '--config-root', $configPath,
            '--snapshot-config', $snapshotConfig
        ) | Out-Null
    }
}

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$rollbackBackup = Join-Path $InstallRoot "backups\rollback_$stamp"
New-Item -ItemType Directory -Path $rollbackBackup -Force | Out-Null
foreach ($path in @($configPath, $currentPath)) {
    if (Test-Path -LiteralPath $path) {
        Copy-Item -LiteralPath $path -Destination $rollbackBackup -Recurse
    }
}

& (Join-Path $PSScriptRoot 'install-windows.ps1') `
    -InstallRoot $InstallRoot `
    -VaultRoot $VaultRoot `
    -Version $Version `
    -SourceRoot $releaseRoot `
    -ConfirmInstall
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if (Test-Path -LiteralPath $targetConfig) {
    Invoke-ConfigLifecycle -Arguments @(
        'restore',
        '--config-root', $configPath,
        '--snapshot-config', $targetConfig,
        '--expected-source-hash', $sourceHash
    ) | Out-Null
} else {
    Invoke-ConfigLifecycle -Arguments @(
        'inspect',
        '--config-root', $configPath,
        '--expected-source-hash', $sourceHash
    ) | Out-Null
}
exit 0
