param([string]$InstallRoot = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'install_common.ps1')
$targetRoot = Assert-AppDirectory $InstallRoot
Assert-ManagedApp $targetRoot
Stop-AppAt $targetRoot
# This root and every contained file have been checked against the managed
# installation marker/manifest. User data is stored outside this directory.
Set-Location (Split-Path $targetRoot -Parent)
Remove-Item -LiteralPath $targetRoot -Recurse -Force
Write-Host 'CodexCue application removed. Settings, credential entries and Ollama models were retained.'
