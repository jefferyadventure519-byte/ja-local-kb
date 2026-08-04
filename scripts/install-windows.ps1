[CmdletBinding()]
param(
    [string]$InstallRoot = 'D:\JA-Local-KB',
    [Parameter(Mandatory = $true)]
    [string]$VaultRoot,
    [string]$Version = '0.1.0',
    [string]$EmbeddingModel = 'text-embedding-3-large',
    [ValidateSet('openai', 'openai_compatible')]
    [string]$EmbeddingProvider = 'openai',
    [string]$EmbeddingBaseUrl = 'https://api.openai.com/v1',
    [int]$EmbeddingDimensions = 0,
    [string]$RerankerModel = '',
    [string]$RerankerBaseUrl = 'https://api.siliconflow.cn/v1',
    [string]$RerankerSecretName = 'embedding_api_key',
    [string]$SourceRoot = '',
    [switch]$ConfirmInstall
)

$ErrorActionPreference = 'Stop'
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

if ([string]::IsNullOrWhiteSpace($SourceRoot)) {
    $SourceRoot = Split-Path -Parent $PSScriptRoot
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath,
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Value
    )

    [System.IO.File]::WriteAllText(
        $LiteralPath,
        $Value + [System.Environment]::NewLine,
        $utf8NoBom
    )
}

if (-not $ConfirmInstall) {
    throw 'Installation requires -ConfirmInstall after preflight review.'
}

$preflight = & (Join-Path $PSScriptRoot 'preflight-windows.ps1') `
    -InstallRoot $InstallRoot `
    -VaultRoot $VaultRoot | ConvertFrom-Json
if (-not $preflight.ok) {
    throw 'Preflight failed. No installation changes were made.'
}

$resolvedVault = (Resolve-Path -LiteralPath $VaultRoot).Path
$releaseRoot = Join-Path $InstallRoot "releases\$Version"
$runtimeRoot = Join-Path $InstallRoot 'runtime'
$configRoot = Join-Path $InstallRoot 'config'
$stateRoot = Join-Path $InstallRoot 'state'
$dataRoot = Join-Path $InstallRoot 'data'
$backupRoot = Join-Path $InstallRoot 'backups'
$pluginRoot = Join-Path $resolvedVault '.obsidian\plugins\ja-local-kb'
$venvRoot = Join-Path $runtimeRoot 'venv'
$python = Join-Path $venvRoot 'Scripts\python.exe'

foreach ($directory in @(
    $releaseRoot,
    $runtimeRoot,
    $configRoot,
    $stateRoot,
    $dataRoot,
    (Join-Path $InstallRoot 'logs'),
    $backupRoot,
    (Join-Path $InstallRoot 'app')
)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$resolvedRelease = (Resolve-Path -LiteralPath $releaseRoot).Path
if ($resolvedSource -ne $resolvedRelease) {
    foreach ($directory in @('src', 'contracts', 'docs', 'scripts')) {
        $destination = Join-Path $releaseRoot $directory
        if (-not (Test-Path -LiteralPath $destination)) {
            Copy-Item -LiteralPath (Join-Path $SourceRoot $directory) `
                -Destination $destination `
                -Recurse
        }
    }
    foreach ($file in @(
        'pyproject.toml',
        'uv.lock',
        'README.md',
        'SECURITY.md',
        'CHANGELOG.md',
        'INSTALL_AGENT.md',
        'UPDATE_AGENT.md'
    )) {
        Copy-Item -LiteralPath (Join-Path $SourceRoot $file) `
            -Destination (Join-Path $releaseRoot $file) `
            -Force
    }
}

$releasePlugin = Join-Path $releaseRoot 'obsidian-plugin'
New-Item -ItemType Directory -Path $releasePlugin -Force | Out-Null
if ($resolvedSource -ne $resolvedRelease) {
    foreach ($file in @(
        'main.js',
        'manifest.json',
        'styles.css',
        'versions.json',
        'j-icon-white.png'
    )) {
        Copy-Item -LiteralPath (Join-Path $SourceRoot "obsidian-plugin\$file") `
            -Destination (Join-Path $releasePlugin $file) `
            -Force
    }
}

$env:UV_PROJECT_ENVIRONMENT = $venvRoot
$env:UV_CACHE_DIR = Join-Path $runtimeRoot 'uv-cache'
& uv sync `
    --project $releaseRoot `
    --extra all `
    --no-dev `
    --no-editable `
    --reinstall-package ja-local-kb
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed with exit code $LASTEXITCODE."
}

$settingsPath = Join-Path $configRoot 'settings.json'
$sourcesPath = Join-Path $configRoot 'sources.json'
if (-not (Test-Path -LiteralPath $sourcesPath)) {
    $sourcesJson = [ordered]@{
        schema_version = 1
        sources = @()
    } | ConvertTo-Json -Depth 4
    Write-Utf8NoBom -LiteralPath $sourcesPath -Value $sourcesJson
}
if (-not (Test-Path -LiteralPath $settingsPath)) {
    $settings = [ordered]@{
        schema_version = 1
        vault_root = $resolvedVault
        runtime_root = $runtimeRoot
        source_registry = $sourcesPath
        state_path = (Join-Path $stateRoot 'state.sqlite3')
        lancedb_root = (Join-Path $dataRoot 'lancedb')
        embedding = [ordered]@{
            provider = $EmbeddingProvider
            model = $EmbeddingModel
            base_url = $EmbeddingBaseUrl
            secret_name = 'embedding_api_key'
            dimensions = $(if ($EmbeddingDimensions -gt 0) {
                $EmbeddingDimensions
            } else {
                $null
            })
            batch_size = 64
            timeout_seconds = 30
            max_retries = 3
        }
        chunking = [ordered]@{
            max_chars = 2200
            overlap_chars = 120
        }
        retrieval = [ordered]@{
            default_mode = 'recall'
            simple_top_k = 8
            complex_candidate_k = 24
            complex_top_k = 24
            smart_candidate_k = 80
            smart_max_subqueries = 6
            smart_vector_subqueries = 6
            recall_candidate_k = 80
            quality_candidate_k = 80
            quality_top_k = 40
            evidence_min = 6
            evidence_max = 40
        }
    }
    if ($RerankerModel) {
        $settings.reranker = [ordered]@{
            provider = 'siliconflow'
            model = $RerankerModel
            base_url = $RerankerBaseUrl
            secret_name = $RerankerSecretName
            instruction = @(
                'Rank each passage by whether it provides specific, current, '
                'directly citable evidence needed to answer the query. Prefer '
                'concrete decisions, rules, constraints, exceptions, state '
                'changes, and source facts over generic topical similarity. '
                'Treat a multi-part query as independent evidence needs: rank '
                'a passage highly when it directly supports any one named '
                'project, sub-question, boundary, or exception, even if it '
                'does not answer the whole query. '
                'Preserve complementary evidence.'
            ) -join ''
            timeout_seconds = 120
            max_retries = 3
        }
    }
    $settingsJson = $settings | ConvertTo-Json -Depth 8
    Write-Utf8NoBom -LiteralPath $settingsPath -Value $settingsJson
}

if (Test-Path -LiteralPath $pluginRoot) {
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    Copy-Item -LiteralPath $pluginRoot `
        -Destination (Join-Path $backupRoot "obsidian-plugin_$stamp") `
        -Recurse
}
New-Item -ItemType Directory -Path $pluginRoot -Force | Out-Null
foreach ($file in @(
    'main.js',
    'manifest.json',
    'styles.css',
    'j-icon-white.png'
)) {
    Copy-Item -LiteralPath (Join-Path $releasePlugin $file) `
        -Destination (Join-Path $pluginRoot $file) `
        -Force
}
$pluginData = Join-Path $pluginRoot 'data.json'
if (-not (Test-Path -LiteralPath $pluginData)) {
    $pluginSettings = [ordered]@{
        pythonExecutable = $python
        settingsPath = $settingsPath
        autoStartWatcher = $true
        statusRefreshSeconds = 10
        graphHighlightEnabled = $true
        backgroundBatch = $null
    }
} else {
    $pluginDataText = [System.IO.File]::ReadAllText($pluginData)
    $pluginDataText = $pluginDataText.TrimStart([char]0xFEFF)
    try {
        $pluginSettings = $pluginDataText | ConvertFrom-Json
    } catch {
        throw "Existing Obsidian plugin data is invalid JSON: $pluginData"
    }
}
$pluginDataJson = $pluginSettings | ConvertTo-Json -Depth 8
Write-Utf8NoBom -LiteralPath $pluginData -Value $pluginDataJson

$currentJson = [ordered]@{
    version = $Version
    release_root = $releaseRoot
    installed_at = (Get-Date).ToString('o')
} | ConvertTo-Json
Write-Utf8NoBom `
    -LiteralPath (Join-Path $InstallRoot 'app\current.json') `
    -Value $currentJson

[ordered]@{
    installed = $true
    version = $Version
    install_root = $InstallRoot
    python = $python
    settings = $settingsPath
    plugin = $pluginRoot
    next_steps = @(
        'Store the Embedding API key with ja-kb set-secret.',
        'Enable ja-local-kb in Obsidian and explicitly add sources.',
        'Generate and review Codex/Claude MCP connection snippets.',
        'Run doctor-windows.ps1.'
    )
} | ConvertTo-Json -Depth 5
