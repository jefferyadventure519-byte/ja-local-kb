[CmdletBinding()]
param(
    [string]$RuntimeRoot = 'D:\JA-Local-KB',
    [Parameter(Mandatory = $true)]
    [string]$VaultRoot,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs = @()
)

$ErrorActionPreference = 'Stop'

if (-not [System.IO.Path]::IsPathFullyQualified($RuntimeRoot)) {
    throw "RuntimeRoot must be an absolute path: $RuntimeRoot"
}
if (-not [System.IO.Path]::IsPathFullyQualified($VaultRoot)) {
    throw "VaultRoot must be an absolute path: $VaultRoot"
}
if (@($PytestArgs | Where-Object {
    $_ -eq '--basetemp' -or $_ -like '--basetemp=*'
}).Count -gt 0) {
    throw 'Do not override --basetemp. The test wrapper keeps pytest outside the Obsidian Vault.'
}

$repoRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$resolvedVault = [System.IO.Path]::GetFullPath($VaultRoot).TrimEnd('\')
$resolvedRuntime = [System.IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\')
$pytestRoot = Join-Path $resolvedRuntime 'tmp\pytest'
$uvCacheRoot = Join-Path $resolvedRuntime 'runtime\uv-cache'

if (
    $pytestRoot.Equals($resolvedVault, [System.StringComparison]::OrdinalIgnoreCase) -or
    $pytestRoot.StartsWith(
        $resolvedVault + '\',
        [System.StringComparison]::OrdinalIgnoreCase
    )
) {
    throw "pytest temp root must stay outside the Obsidian Vault: $pytestRoot"
}

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$runRoot = Join-Path $pytestRoot "run_${stamp}_$PID"
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
New-Item -ItemType Directory -Path $uvCacheRoot -Force | Out-Null

$env:UV_CACHE_DIR = $uvCacheRoot
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

Push-Location $repoRoot
try {
    & uv sync `
        --all-extras `
        --no-editable `
        --reinstall-package ja-local-kb
    $syncExitCode = $LASTEXITCODE

    if ($syncExitCode -eq 0) {
        & uv run --no-sync pytest `
            -q `
            -p no:cacheprovider `
            --basetemp $runRoot `
            @PytestArgs
        $pytestExitCode = $LASTEXITCODE
    } else {
        $pytestExitCode = $null
    }
} finally {
    Pop-Location
}

$finalExitCode = if ($syncExitCode -ne 0) {
    $syncExitCode
} else {
    $pytestExitCode
}

[ordered]@{
    ok = $finalExitCode -eq 0
    exit_code = $finalExitCode
    sync_exit_code = $syncExitCode
    pytest_exit_code = $pytestExitCode
    repo_root = $repoRoot
    vault_root = $resolvedVault
    pytest_temp = $runRoot
    pytest_temp_outside_vault = $true
} | ConvertTo-Json

exit $finalExitCode
