<#
.SYNOPSIS
  Install CodexCue and its local Ollama backend on Windows.
.DESCRIPTION
  Running this script explicitly authorizes downloading Ollama and the model.
  The Ollama archive is checked against the official release SHA256 manifest.
  Installation stays in this checkout and needs no administrator rights.
#>
param(
    [switch]$SkipModel,
    [switch]$SkipOllama,
    [switch]$AppOnly,
    [switch]$ForceUvInstall,
    [switch]$Build,
    [switch]$Start,
    [string]$OllamaModel = 'qwen3:4b-instruct',
    [string]$ProxyUrl = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$local = Join-Path $repo '.local'
$ollamaDir = Join-Path $local 'ollama'
$modelDir = Join-Path $local 'models'
$archive = Join-Path $local 'ollama-windows-amd64.zip'
$model = $OllamaModel.Trim()
if (-not $model -or $model -notmatch '^[a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$') {
    throw 'Setup failed: OllamaModel must be a valid model name or model:tag.'
}
if ($ProxyUrl) {
    $env:HTTPS_PROXY = $ProxyUrl
    $env:HTTP_PROXY = $ProxyUrl
    $env:NO_PROXY = '127.0.0.1,localhost,::1'
}

function Say([string]$message) { Write-Host "[setup] $message" }
function Fail([string]$message) { throw "Setup failed: $message" }
function Find-Uv {
    $localUv = Join-Path $local 'bin\uv.exe'
    if (Test-Path -LiteralPath $localUv) { return $localUv }
    $installedUv = Get-Command uv -ErrorAction SilentlyContinue
    if ($installedUv) { return $installedUv.Source }
    return $null
}
function Install-Uv {
    $installer = Join-Path $local 'install-uv.ps1'
    $url = 'https://astral.sh/uv/install.ps1'
    Say "Downloading the official uv installer from $url"
    $webArgs = @{ Uri = $url; OutFile = $installer; UseBasicParsing = $true; TimeoutSec = 60 }
    if ($ProxyUrl) { $webArgs.Proxy = $ProxyUrl }
    Invoke-WebRequest @webArgs
    $env:UV_INSTALL_DIR = Join-Path $local 'bin'
    $env:UV_NO_MODIFY_PATH = '1'
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer | Out-Host
        if ($LASTEXITCODE -ne 0) { Fail 'The uv installer failed.' }
    } finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
    $uvPath = Join-Path $env:UV_INSTALL_DIR 'uv.exe'
    if (-not (Test-Path -LiteralPath $uvPath)) { Fail 'The uv installer did not create uv.exe.' }
    return $uvPath
}
function Wait-Ollama {
    for ($i = 0; $i -lt 60; $i++) {
        & curl.exe --silent --fail --noproxy '*' --max-time 2 'http://127.0.0.1:11434/api/version' | Out-Null
        if ($LASTEXITCODE -eq 0) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

if ($env:OS -ne 'Windows_NT') { Fail 'Windows is required.' }
if (-not $AppOnly -and -not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { Fail 'curl.exe is required.' }
if ($Build) {
    $packageExe = Join-Path $repo 'dist\CodexCue\CodexCue.exe'
    $inUse = @(Get-Process CodexCue -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $packageExe })
    if ($inUse.Count -gt 0) { Fail 'Close the running packaged app before rebuilding dist\CodexCue.' }
}
New-Item -ItemType Directory -Path $local, $modelDir -Force | Out-Null

# uv provisions a private Python 3.12 when Python is not installed.
$uv = if ($ForceUvInstall) { $null } else { Find-Uv }
if (-not $uv) { $uv = Install-Uv }
Push-Location $repo
try {
    Say 'Installing Python 3.12 and locked project dependencies with uv.'
    $env:UV_CACHE_DIR = Join-Path $local 'uv-cache'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $local 'python'
    $uvArgs = @('sync', '--python', '3.12', '--locked', '--extra', 'test')
    if ($Build) { $uvArgs += @('--extra', 'build') }
    & $uv @uvArgs
    if ($LASTEXITCODE -ne 0) { Fail 'uv sync failed. Check network access or supply -ProxyUrl.' }
} finally { Pop-Location }

$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { Fail 'Python virtual environment was not created.' }
& $python -c 'import PySide6, httpx, uiautomation, psutil, keyring, codex_companion'
if ($LASTEXITCODE -ne 0) { Fail 'Installed application failed its import check.' }

if ($AppOnly) {
    Say 'Application installed. Configure an OpenAI-compatible backend in the Settings menu.'
} else {
$ollamaExe = $null
$installed = Get-Command ollama -ErrorAction SilentlyContinue
if ($installed) { $ollamaExe = $installed.Source }
if (-not $ollamaExe) {
    $found = Get-ChildItem -LiteralPath $ollamaDir -Filter ollama.exe -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { $ollamaExe = $found.FullName }
}

if (-not $ollamaExe -and -not $SkipOllama) {
    Say 'Downloading official Ollama Windows archive (about 1.5 GB).'
    $base = 'https://github.com/ollama/ollama/releases/latest/download'
    $webArgs = @{ Uri = "$base/sha256sum.txt"; UseBasicParsing = $true; TimeoutSec = 60 }
    if ($ProxyUrl) { $webArgs.Proxy = $ProxyUrl }
    $manifest = Invoke-WebRequest @webArgs
    $manifestText = if ($manifest.Content -is [byte[]]) {
        [Text.Encoding]::UTF8.GetString($manifest.Content)
    } else { [string]$manifest.Content }
    $match = [regex]::Match($manifestText, '(?m)^([0-9a-f]{64})\s+\./ollama-windows-amd64\.zip\s*$')
    if (-not $match.Success) { Fail 'Official SHA256 manifest has no Windows archive entry.' }
    $expectedHash = $match.Groups[1].Value
    $hasValidArchive = (Test-Path $archive) -and
        ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -eq $expectedHash)
    if (-not $hasValidArchive) {
        $curlArgs = @('--fail', '--location', '--retry', '3', '--continue-at', '-', '--silent', '--show-error')
        if ($ProxyUrl) { $curlArgs += @('--proxy', $ProxyUrl) }
        & curl.exe @curlArgs --output $archive "$base/ollama-windows-amd64.zip"
        if ($LASTEXITCODE -ne 0) { Fail 'Ollama archive download failed.' }
    }
    $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) { Fail 'Ollama archive SHA256 mismatch.' }
    Say 'Archive verified. Extracting Ollama.'
    New-Item -ItemType Directory -Path $ollamaDir -Force | Out-Null
    Expand-Archive -LiteralPath $archive -DestinationPath $ollamaDir -Force
    $found = Get-ChildItem -LiteralPath $ollamaDir -Filter ollama.exe -Recurse -File | Select-Object -First 1
    if (-not $found) { Fail 'ollama.exe not found after extraction.' }
    $ollamaExe = $found.FullName
    Remove-Item -LiteralPath $archive -Force
}
if (-not $ollamaExe) { Fail 'Ollama is missing. Re-run without -SkipOllama.' }

$env:OLLAMA_HOST = '127.0.0.1:11434'
if ($ollamaExe.StartsWith($ollamaDir, [StringComparison]::OrdinalIgnoreCase)) {
    $env:OLLAMA_MODELS = $modelDir
}
if (-not (Wait-Ollama)) {
    Say 'Starting Ollama on 127.0.0.1:11434.'
    Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden | Out-Null
    if (-not (Wait-Ollama)) { Fail 'Ollama did not start on 127.0.0.1:11434.' }
}

if (-not $SkipModel) {
    $tagsJson = & curl.exe --silent --fail --noproxy '*' 'http://127.0.0.1:11434/api/tags'
    if ($LASTEXITCODE -ne 0) { Fail 'Could not query Ollama models.' }
    $tags = $tagsJson | ConvertFrom-Json
    $present = @($tags.models | Where-Object { $_.name -eq $model })
    if ($present.Count -eq 0) {
        Say "Downloading $model (download size depends on the selected model; the default is about 2.5 GB)."
        & $ollamaExe pull $model
        if ($LASTEXITCODE -ne 0) { Fail 'Model download failed.' }
    }
    $verifiedTagsJson = & curl.exe --silent --fail --noproxy '*' 'http://127.0.0.1:11434/api/tags'
    if ($LASTEXITCODE -ne 0) { Fail 'Could not verify installed Ollama models.' }
    $verifiedTags = $verifiedTagsJson | ConvertFrom-Json
    if (@($verifiedTags.models | Where-Object { $_.name -eq $model }).Count -eq 0) {
        Fail "Model $model is still missing after setup."
    }
}

$env:COMPANION_OLLAMA_EXE = $ollamaExe
$env:COMPANION_OLLAMA_MODELS = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { '' }
$env:COMPANION_OLLAMA_MODEL = $model
& $python -m codex_companion.setup_config
if ($LASTEXITCODE -ne 0) { Fail 'Could not save application configuration.' }
}

if ($Build) {
    Say 'Building Windows executable.'
    Push-Location $repo
    try { & $python -m PyInstaller --noconfirm --clean codexcue.spec }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { Fail 'Windows executable build failed.' }
    & $python (Join-Path $repo 'scripts\check_package.py')
    if ($LASTEXITCODE -ne 0) { Fail 'Packaged Qt libraries failed to load.' }
}
if ($Start) {
    $appExe = if ($Build) {
        Join-Path $repo 'dist\CodexCue\CodexCue.exe'
    } else {
        Join-Path $repo '.venv\Scripts\pythonw.exe'
    }
    if (-not (Test-Path -LiteralPath $appExe)) { Fail "Application launcher is missing: $appExe" }
    $startArgs = @{ FilePath = $appExe; WorkingDirectory = $repo; WindowStyle = 'Hidden' }
    $startArgs.ArgumentList = if ($Build) { '--background' } else { @('run_companion.py', '--background') }
    Start-Process @startArgs | Out-Null
    Say 'Application started. Look for its tray icon.'
} else {
    Say 'Configuration complete. Run .\.venv\Scripts\codexcue.exe or use -Start.'
}
