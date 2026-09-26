$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\scripts\download.ps1')
$root = Join-Path ([IO.Path]::GetTempPath()) ('codexcue-download-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$good = Join-Path $root 'good'
[IO.File]::WriteAllText($good, 'verified fixture archive')
$hash = (Get-FileHash -LiteralPath $good).Hash.ToLowerInvariant()
$destination = Join-Path $root 'archive.zip'
$script:requests = @()
$script:responses = @()
function curl.exe {
    $arguments = @($args)
    $script:requests += ,$arguments
    $target = $arguments[[array]::IndexOf($arguments, '--output') + 1]
    $response = $script:responses[0]
    $script:responses = @($script:responses | Select-Object -Skip 1)
    [IO.File]::WriteAllText($target, $response.body)
    $global:LASTEXITCODE = $response.code
    return $response.status
}
try {
    # A corrupt complete archive must trigger one fresh retry, without resume.
    [IO.File]::WriteAllText($destination, 'old corrupt archive')
    $script:responses = @(@{body='bad';code=0;status='200'}, @{body='verified fixture archive';code=0;status='200'})
    Receive-VerifiedArchive -Url 'https://example.test/v1.zip' -Destination $destination -Sha256 $hash
    if ($script:requests.Count -ne 2 -or ($script:requests[1] -contains '--continue-at')) { throw 'Corrupt archive was resumed.' }
    Remove-Item -LiteralPath $destination
    # Matching interrupted downloads resume; unsupported ranges restart fresh.
    [IO.File]::WriteAllText(($destination + '.part'), 'partial')
    @{url='https://example.test/v1.zip';sha256=$hash} | ConvertTo-Json | Set-Content -LiteralPath ($destination + '.download.json')
    $script:requests = @()
    $script:responses = @(@{body='partial';code=33;status='416'}, @{body='verified fixture archive';code=0;status='200'})
    Receive-VerifiedArchive -Url 'https://example.test/v1.zip' -Destination $destination -Sha256 $hash
    if (-not ($script:requests[0] -contains '--continue-at') -or ($script:requests[1] -contains '--continue-at')) { throw 'Range fallback failed.' }
    Remove-Item -LiteralPath $destination
    # A different release must never be appended to an older partial download.
    [IO.File]::WriteAllText(($destination + '.part'), 'partial')
    @{url='https://example.test/old.zip';sha256=$hash} | ConvertTo-Json | Set-Content -LiteralPath ($destination + '.download.json')
    $script:requests = @()
    $script:responses = @(@{body='verified fixture archive';code=0;status='200'})
    Receive-VerifiedArchive -Url 'https://example.test/new.zip' -Destination $destination -Sha256 $hash
    if ($script:requests[0] -contains '--continue-at') { throw 'Resumed a different release.' }
    Write-Host 'PASS: corrupt archive retry, range fallback, release change.'
} finally {
    $resolved = [IO.Path]::GetFullPath($root)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath())) -or (Split-Path $resolved -Leaf) -notlike 'codexcue-download-*') { throw 'Unsafe fixture cleanup.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
