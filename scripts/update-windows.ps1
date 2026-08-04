[CmdletBinding()]
param(
    [string]$InstallRoot = 'D:\JA-Local-KB',
    [Parameter(Mandatory = $true)]
    [string]$VaultRoot,
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [string]$SourceRoot = (Split-Path -Parent $PSScriptRoot),
    [switch]$ConfirmUpdate
)

$ErrorActionPreference = 'Stop'
if (-not $ConfirmUpdate) {
    throw 'Update requires -ConfirmUpdate after release and preflight review.'
}

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

function Save-VersionConfigSnapshot {
    param([string]$Root)
    $currentPath = Join-Path $Root 'app\current.json'
    $configPath = Join-Path $Root 'config'
    if (
        -not (Test-Path -LiteralPath $currentPath) -or
        -not (Test-Path -LiteralPath $configPath)
    ) {
        return
    }
    $current = Get-Content -Raw -Encoding utf8 -LiteralPath $currentPath |
        ConvertFrom-Json
    $currentVersion = [string]$current.version
    if ($currentVersion -notmatch '^[A-Za-z0-9._-]+$') {
        throw "Unsafe installed version identifier: $currentVersion"
    }
    $snapshotRoot = Join-Path $Root "backups\versions\$currentVersion"
    $snapshotConfig = Join-Path $snapshotRoot 'config'
    if (-not (Test-Path -LiteralPath $snapshotConfig)) {
        Invoke-ConfigLifecycle -Arguments @(
            'snapshot',
            '--config-root', $configPath,
            '--snapshot-config', $snapshotConfig
        ) | Out-Null
    }
}

$configPath = Join-Path $InstallRoot 'config'
$sourceState = Invoke-ConfigLifecycle -Arguments @(
    'inspect',
    '--config-root', $configPath
)
$sourceHash = [string]$sourceState.registry.sha256
Save-VersionConfigSnapshot -Root $InstallRoot
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backup = Join-Path $InstallRoot "backups\update_$stamp"
New-Item -ItemType Directory -Path $backup -Force | Out-Null
foreach ($path in @(
    (Join-Path $InstallRoot 'config'),
    (Join-Path $InstallRoot 'app\current.json')
)) {
    if (Test-Path -LiteralPath $path) {
        Copy-Item -LiteralPath $path -Destination $backup -Recurse
    }
}
& (Join-Path $PSScriptRoot 'install-windows.ps1') `
    -InstallRoot $InstallRoot `
    -VaultRoot $VaultRoot `
    -Version $Version `
    -SourceRoot $SourceRoot `
    -ConfirmInstall
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
Invoke-ConfigLifecycle -Arguments @(
    'inspect',
    '--config-root', $configPath,
    '--expected-source-hash', $sourceHash
) | Out-Null
exit 0
