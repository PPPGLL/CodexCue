# Exercise the missing-uv branch without downloading anything during this test.
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$fixture = Join-Path $repo '.local\bootstrap-fixture'
$localUv = Join-Path $repo '.local\bin\uv.exe'
$uv = if (Test-Path -LiteralPath $localUv) { $localUv } else {
    (Get-Command uv -ErrorAction Stop).Source
}
New-Item -ItemType Directory -Path $fixture -Force | Out-Null
$env:COMPANION_TEST_UV_SOURCE = Join-Path $fixture 'uv.exe'
Copy-Item -LiteralPath $uv -Destination $env:COMPANION_TEST_UV_SOURCE -Force
$fakeInstaller = Join-Path $fixture 'install-uv.ps1'
@'
New-Item -ItemType Directory -Path $env:UV_INSTALL_DIR -Force | Out-Null
Copy-Item -LiteralPath $env:COMPANION_TEST_UV_SOURCE -Destination (Join-Path $env:UV_INSTALL_DIR 'uv.exe') -Force
Write-Output 'mock uv installer ran'
'@ | Set-Content -LiteralPath $fakeInstaller -Encoding UTF8

function Invoke-WebRequest {
    param($Uri, $OutFile, $UseBasicParsing, $TimeoutSec, $Proxy)
    if ($Uri -ne 'https://astral.sh/uv/install.ps1') { throw "Unexpected URL: $Uri" }
    Copy-Item -LiteralPath $fakeInstaller -Destination $OutFile -Force
}

. (Join-Path $repo 'scripts\setup.ps1') -EnvironmentOnly -ForceUvInstall
if (-not (Test-Path -LiteralPath $localUv)) { throw 'Local uv was not installed.' }
