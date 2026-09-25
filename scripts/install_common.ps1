$ErrorActionPreference = 'Stop'
function Assert-AppDirectory([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ((Split-Path $full -Parent) -eq '' -or $full -eq [IO.Path]::GetPathRoot($full).TrimEnd('\')) { throw 'Refusing a filesystem root.' }
    foreach ($reserved in @($env:USERPROFILE, $env:LOCALAPPDATA, $env:ProgramFiles, $env:SystemRoot)) {
        if ($reserved -and $full -eq [IO.Path]::GetFullPath($reserved).TrimEnd('\')) { throw 'Refusing a system/user data root.' }
    }
    $current = $full
    while ($current) {
        if ((Test-Path -LiteralPath $current) -and ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Reparse points are not supported in install paths.' }
        $current = Split-Path $current -Parent
    }
    if (Test-Path -LiteralPath $full) {
        foreach ($entry in Get-ChildItem -LiteralPath $full -Force -Recurse) {
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse points are not supported inside an installation.' }
        }
    }
    return $full
}
function Get-AppManifest([string]$Root) {
    $manifest = [IO.File]::ReadAllText((Join-Path $Root 'release.json')) | ConvertFrom-Json
    if ($manifest.application -ne 'CodexCue' -or -not $manifest.files.'CodexCue.exe') { throw 'Not a CodexCue release.' }
    foreach ($property in $manifest.files.PSObject.Properties) {
        $path = [IO.Path]::GetFullPath((Join-Path $Root $property.Name))
        if (-not $path.StartsWith($Root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Release path escapes its directory.' }
        if ($property.Value -notmatch '^[0-9a-f]{64}$') { throw 'Invalid file checksum.' }
    }
    return $manifest
}
function Assert-ManagedApp([string]$Root) {
    $marker = [IO.File]::ReadAllText((Join-Path $Root 'codexcue-install.json')) | ConvertFrom-Json
    if ($marker.application -ne 'CodexCue') { throw 'Refusing an unmanaged directory.' }
    $manifest = Get-AppManifest $Root
    $known = @{'release.json'=$true; 'codexcue-install.json'=$true}
    foreach ($property in $manifest.files.PSObject.Properties) { $known[$property.Name.Replace('/', '\')] = $true }
    foreach ($file in Get-ChildItem -LiteralPath $Root -File -Force -Recurse) {
        $relative = $file.FullName.Substring($Root.Length + 1)
        if (-not $known.ContainsKey($relative)) { throw "Unrecognized file in app folder; preserve/move it before maintenance: $relative" }
    }
}
function Stop-AppAt([string]$Root) {
    $exe = Join-Path $Root 'CodexCue.exe'
    foreach ($process in @(Get-Process CodexCue -ErrorAction SilentlyContinue)) {
        if ($process.Path -eq $exe) {
            Stop-Process -Id $process.Id -ErrorAction Stop
            if (-not $process.WaitForExit(10000)) { throw 'Application did not stop.' }
        }
    }
}
