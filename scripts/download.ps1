# Shared by setup and offline download-recovery tests. Never execute downloaded code.
function Receive-VerifiedArchive {
    param([string]$Url, [string]$Destination, [string]$Sha256, [string]$ProxyUrl = '')
    $ErrorActionPreference = 'Stop'
    if ($Sha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'Invalid expected SHA256.' }
    $Sha256 = $Sha256.ToLowerInvariant()
    if (Test-Path -LiteralPath $Destination) {
        if ((Get-FileHash -LiteralPath $Destination).Hash.ToLowerInvariant() -eq $Sha256) { return }
        Remove-Item -LiteralPath $Destination -Force
    }
    $part = $Destination + '.part'
    $metadata = $Destination + '.download.json'
    $matches = $false
    if (Test-Path -LiteralPath $metadata) {
        try {
            $saved = [IO.File]::ReadAllText($metadata) | ConvertFrom-Json
            $matches = $saved.url -eq $Url -and $saved.sha256 -eq $Sha256
        } catch { $matches = $false }
    }
    if (-not $matches -and (Test-Path -LiteralPath $part)) { Remove-Item -LiteralPath $part -Force }
    @{url=$Url; sha256=$Sha256} | ConvertTo-Json | Set-Content -LiteralPath $metadata -Encoding UTF8
    for ($attempt = 0; $attempt -lt 2; $attempt++) {
        $resume = (Test-Path -LiteralPath $part) -and (Get-Item -LiteralPath $part).Length -gt 0
        $downloadArgs = @('--fail', '--location', '--retry', '3', '--connect-timeout', '20', '--silent', '--show-error')
        if ($resume) { $downloadArgs += @('--continue-at', '-') }
        if ($ProxyUrl) { $downloadArgs += @('--proxy', $ProxyUrl) }
        $httpStatus = & curl.exe @downloadArgs --write-out '%{http_code}' --output $part $Url
        $downloadExit = $LASTEXITCODE
        if ($downloadExit -ne 0) {
            if ($attempt -eq 0 -and $resume -and ($downloadExit -eq 33 -or "$httpStatus" -eq '416')) {
                Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
                continue
            }
            throw "Archive download failed (curl=$downloadExit, HTTP=$httpStatus). Rerun setup to resume."
        }
        if ((Get-FileHash -LiteralPath $part).Hash.ToLowerInvariant() -eq $Sha256) {
            Move-Item -LiteralPath $part -Destination $Destination
            Remove-Item -LiteralPath $metadata -Force
            return
        }
        # Never resume a complete archive whose contents failed verification.
        Remove-Item -LiteralPath $part -Force
    }
    throw 'Archive SHA256 mismatch after a fresh retry; no archive was extracted.'
}
