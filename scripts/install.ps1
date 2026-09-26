param(
    [string]$Source = $PSScriptRoot,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\CodexCue'),
    [switch]$Start
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'install_common.ps1')
$sourceRoot = Assert-AppDirectory $Source
$targetRoot = Assert-AppDirectory $InstallRoot
if ($sourceRoot -eq $targetRoot -or $sourceRoot.StartsWith($targetRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Extract the new release outside the installation directory.' }
$manifest = Get-AppManifest $sourceRoot
foreach ($property in $manifest.files.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath (Join-Path $sourceRoot $property.Name)).Hash.ToLowerInvariant() -ne $property.Value) { throw "Release checksum mismatch: $($property.Name)" }
}
if (Test-Path -LiteralPath $targetRoot) { Assert-ManagedApp $targetRoot }
$parent = Split-Path $targetRoot -Parent
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$stage = Assert-AppDirectory (Join-Path $parent ('CodexCue.stage-' + [guid]::NewGuid().ToString('N')))
$backup = Assert-AppDirectory (Join-Path $parent ('CodexCue.previous-' + [guid]::NewGuid().ToString('N')))
if ((Split-Path $stage -Parent) -ne $parent -or (Split-Path $backup -Parent) -ne $parent) { throw 'Maintenance paths escaped the install parent.' }
$swapped = $false
try {
    New-Item -ItemType Directory -Path $stage | Out-Null
    foreach ($property in $manifest.files.PSObject.Properties) {
        $destination = Join-Path $stage $property.Name
        New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $sourceRoot $property.Name) -Destination $destination
        if ((Get-FileHash -LiteralPath $destination).Hash.ToLowerInvariant() -ne $property.Value) { throw 'Staged file checksum mismatch.' }
    }
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'release.json') -Destination $stage
    @{application='CodexCue';version=$manifest.version;commit=$manifest.commit} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stage 'codexcue-install.json') -Encoding UTF8
    Stop-AppAt $targetRoot
    if (Test-Path -LiteralPath $targetRoot) { Move-Item -LiteralPath $targetRoot -Destination $backup }
    Move-Item -LiteralPath $stage -Destination $targetRoot
    $swapped = $true
} catch {
    if ((Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $targetRoot)) { Move-Item -LiteralPath $backup -Destination $targetRoot }
    throw
} finally {
    if (Test-Path -LiteralPath $stage) { $null = Assert-AppDirectory $stage; Remove-Item -LiteralPath $stage -Recurse -Force }
}
if ($swapped -and (Test-Path -LiteralPath $backup)) {
    $null = Assert-AppDirectory $backup
    Assert-ManagedApp $backup
    try { Remove-Item -LiteralPath $backup -Recurse -Force } catch { Write-Warning "Upgrade succeeded; previous application files remain at $backup" }
}
if ($Start) { Start-Process -FilePath (Join-Path $targetRoot 'CodexCue.exe') -ArgumentList '--background' -WorkingDirectory $targetRoot -WindowStyle Hidden | Out-Null }
Write-Host "Installed CodexCue $($manifest.version) to $targetRoot. Existing user settings, credentials and models were preserved."
