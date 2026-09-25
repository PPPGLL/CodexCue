param([string]$Bundle = '')
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$root = Join-Path $repo ('.local\install-test-' + [guid]::NewGuid().ToString('N'))
$source = Join-Path $root 'release'
$installed = Join-Path $root 'Programs\CodexCue'
$data = Join-Path $root 'data'
New-Item -ItemType Directory -Path $source,$data -Force | Out-Null
$config = Join-Path $data 'config.json'
[IO.File]::WriteAllText($config, '{"enabled":false,"ollama_model":"keep:1"}')
$configHash = (Get-FileHash -LiteralPath $config).Hash
if ($Bundle) {
    Copy-Item -Path (Join-Path ([IO.Path]::GetFullPath($Bundle)) '*') -Destination $source -Recurse -Force
} else {
    [IO.File]::WriteAllText((Join-Path $source 'CodexCue.exe'), 'synthetic version one')
    foreach ($name in @('install.ps1','install_common.ps1','uninstall.ps1')) { Copy-Item -LiteralPath (Join-Path $repo ('scripts\' + $name)) -Destination $source }
    $files = @{}
    foreach ($file in Get-ChildItem -LiteralPath $source -File) { $files[$file.Name] = (Get-FileHash -LiteralPath $file.FullName).Hash.ToLowerInvariant() }
    @{application='CodexCue';version='fixture1';commit='fixture';files=$files} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $source 'release.json') -Encoding UTF8
}
function Install-TestPackage { & (Join-Path $source 'install.ps1') -Source $source -InstallRoot $installed }
try {
    Install-TestPackage
    $originalHash = (Get-FileHash -LiteralPath (Join-Path $installed 'CodexCue.exe')).Hash
    # Tampered release must leave the current install untouched.
    [IO.File]::AppendAllText((Join-Path $source 'CodexCue.exe'), 'tampered')
    $rejected = $false
    try { Install-TestPackage } catch { $rejected = $true }
    if (-not $rejected -or (Get-FileHash -LiteralPath (Join-Path $installed 'CodexCue.exe')).Hash -ne $originalHash) { throw 'Tampered upgrade was not rejected safely.' }
    # Prepare a valid changed version, then simulate failure at the final swap.
    $manifest = [IO.File]::ReadAllText((Join-Path $source 'release.json')) | ConvertFrom-Json
    $manifest.files.'CodexCue.exe' = (Get-FileHash -LiteralPath (Join-Path $source 'CodexCue.exe')).Hash.ToLowerInvariant()
    $manifest.version = 'fixture2'
    $manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $source 'release.json') -Encoding UTF8
    $global:CodexCueTestFailSwap = $true
    function Move-Item {
        param([string]$LiteralPath, [string]$Destination)
        if ($global:CodexCueTestFailSwap -and (Split-Path $LiteralPath -Leaf) -like 'CodexCue.stage-*') {
            $global:CodexCueTestFailSwap = $false
            throw 'Simulated rename failure'
        }
        Microsoft.PowerShell.Management\Move-Item -LiteralPath $LiteralPath -Destination $Destination
    }
    $rejected = $false
    try { Install-TestPackage } catch { $rejected = $true }
    Remove-Item Function:\Move-Item
    if (-not $rejected -or (Get-FileHash -LiteralPath (Join-Path $installed 'CodexCue.exe')).Hash -ne $originalHash) { throw 'Failed swap did not roll back.' }
    Install-TestPackage
    if ((Get-FileHash -LiteralPath (Join-Path $installed 'CodexCue.exe')).Hash -eq $originalHash) { throw 'Upgrade did not apply.' }
    [IO.File]::WriteAllText((Join-Path $installed 'personal.txt'), 'preserve me')
    $rejected = $false
    try { & (Join-Path $source 'uninstall.ps1') -InstallRoot $installed } catch { $rejected = $true }
    if (-not $rejected -or -not (Test-Path -LiteralPath (Join-Path $installed 'personal.txt'))) { throw 'Uninstaller removed unrecognized user content.' }
    Remove-Item -LiteralPath (Join-Path $installed 'personal.txt')
    & (Join-Path $source 'uninstall.ps1') -InstallRoot $installed
    if (Test-Path -LiteralPath $installed) { throw 'Managed uninstall failed.' }
    if ((Get-FileHash -LiteralPath $config).Hash -ne $configHash) { throw 'User configuration changed during maintenance.' }
    Write-Host 'PASS: fresh install, tamper rejection, rollback, upgrade, safe uninstall, retained user data.'
} finally {
    Set-Location $repo
    $resolved = [IO.Path]::GetFullPath($root)
    if (-not $resolved.StartsWith((Join-Path $repo '.local') + '\') -or (Split-Path $resolved -Leaf) -notlike 'install-test-*') { throw 'Unsafe fixture cleanup.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
