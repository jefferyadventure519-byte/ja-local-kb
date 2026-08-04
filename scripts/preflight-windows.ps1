[CmdletBinding()]
param(
    [string]$InstallRoot = 'D:\JA-Local-KB',
    [Parameter(Mandatory = $true)]
    [string]$VaultRoot
)

$ErrorActionPreference = 'Stop'
$resolvedVault = Resolve-Path -LiteralPath $VaultRoot -ErrorAction Stop
$drive = Split-Path -Qualifier $InstallRoot
$driveInfo = try {
    [System.IO.DriveInfo]::new("$drive\")
} catch {
    $null
}
$uv = Get-Command uv -ErrorAction SilentlyContinue
$git = Get-Command git -ErrorAction SilentlyContinue
$legacyIndex = Join-Path $InstallRoot 'data\lancedb'
$productMarker = Join-Path $InstallRoot 'app\current.json'

$checks = @(
    [ordered]@{
        name = 'install_on_d_drive'
        ok = $drive -eq 'D:'
        value = $InstallRoot
    },
    [ordered]@{
        name = 'vault_exists'
        ok = (Test-Path -LiteralPath $resolvedVault -PathType Container)
        value = [string]$resolvedVault
    },
    [ordered]@{
        name = 'uv_available'
        ok = $null -ne $uv
        value = if ($uv) { $uv.Source } else { '' }
    },
    [ordered]@{
        name = 'git_available'
        ok = $null -ne $git
        value = if ($git) { $git.Source } else { '' }
    },
    [ordered]@{
        name = 'free_space_gb'
        ok = $null -ne $driveInfo -and $driveInfo.AvailableFreeSpace -ge 5GB
        value = if ($driveInfo) {
            [math]::Round($driveInfo.AvailableFreeSpace / 1GB, 2)
        } else {
            0
        }
    },
    [ordered]@{
        name = 'no_legacy_path_collision'
        ok = -not (
            (Test-Path -LiteralPath $legacyIndex) -and
            -not (Test-Path -LiteralPath $productMarker -PathType Leaf)
        )
        value = if (
            (Test-Path -LiteralPath $legacyIndex) -and
            -not (Test-Path -LiteralPath $productMarker -PathType Leaf)
        ) {
            "Existing unowned index: $legacyIndex"
        } else {
            'clear'
        }
    }
)

[ordered]@{
    ok = -not ($checks.ok -contains $false)
    platform = 'windows'
    architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
    install_root = $InstallRoot
    vault_root = [string]$resolvedVault
    planned_writes = @(
        $InstallRoot,
        (Join-Path $resolvedVault '.obsidian\plugins\ja-local-kb')
    )
    checks = $checks
} | ConvertTo-Json -Depth 6
