param(
    [string]$PackagedExe = '',
    [ValidatePattern('^[A-Za-z0-9_./:-]*$')][string]$LiveModel = '',
    [ValidateRange(1, 20)][int]$Repeat = 3,
    [string]$Output = ''
)

$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run setup first; the project Python environment is missing.' }
if (-not $Output) {
    $Output = Join-Path $repo ('.local\verify-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6))
}
$outputRoot = [IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $outputRoot) { throw "Use a new output directory: $outputRoot" }
New-Item -ItemType Directory -Path $outputRoot | Out-Null
$report = [ordered]@{
    schema_version = 1
    status = 'FAIL'
    started_at = [DateTime]::UtcNow.ToString('o')
    target = $(if ($PackagedExe) { 'packaged' } else { 'source' })
    repeat = $Repeat
    live_model = $LiveModel
    source_commit = (& git -C $repo rev-parse HEAD)
    checks = @()
    errors = @()
}
$resultCode = 1
Push-Location $repo
try {
    if ($PackagedExe) {
        $PackagedExe = (Resolve-Path -LiteralPath $PackagedExe).Path
        $report.executable_sha256 = (Get-FileHash -LiteralPath $PackagedExe -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    Write-Host 'Running regression tests (synthetic data only)...'
    $unitXml = Join-Path $outputRoot 'unit-tests.xml'
    & $python -m pytest -p no:cacheprovider -o addopts= -q --basetemp (Join-Path $outputRoot 'pytest-temp') --junitxml $unitXml
    if ($LASTEXITCODE -ne 0) { throw "Regression tests failed with exit code $LASTEXITCODE" }
    [xml]$unitResults = [IO.File]::ReadAllText($unitXml)
    $report.checks += @{ name = 'unit_tests'; status = 'PASS'; tests = [int]$unitResults.testsuites.testsuite.tests }
    foreach ($check in @(@{script='test_download.ps1';name='download_recovery'}, @{script='test_install.ps1';name='install_lifecycle'})) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo ('tests\' + $check.script))
        if ($LASTEXITCODE -ne 0) { throw "$($check.name) failed." }
        $report.checks += @{name=$check.name;status='PASS'}
    }

    if ($PackagedExe) {
        & $python scripts\check_package.py (Split-Path -Parent $PackagedExe)
        if ($LASTEXITCODE -ne 0) { throw "Package dependency check failed with exit code $LASTEXITCODE" }
        $report.checks += @{ name = 'package_dependencies'; status = 'PASS' }
    }
    $runLimit = $Repeat
    if ($LiveModel) { $runLimit++ }
    for ($run = 1; $run -le $runLimit; $run++) {
        $isLive = $run -gt $Repeat
        $runName = if ($isLive) { 'live-model' } else { "desktop-{0:00}" -f $run }
        $runOutput = Join-Path $outputRoot $runName
        $receiptPath = Join-Path $runOutput 'receipt.json'
        Write-Host "$runName acceptance (opens its own synthetic window)..."
        if ($PackagedExe) {
            $program = $PackagedExe
            $arguments = '--self-test --output "' + $runOutput + '"'
        } else {
            $program = $python
            $arguments = '-m codex_companion --self-test --output "' + $runOutput + '"'
        }
        if ($isLive) { $arguments += ' --live-model "' + $LiveModel + '"' }
        $testProcess = Start-Process -FilePath $program -ArgumentList $arguments -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $outputRoot ("desktop-{0:00}.stdout.log" -f $run)) `
            -RedirectStandardError (Join-Path $outputRoot ("desktop-{0:00}.stderr.log" -f $run))
        $null = $testProcess.Handle # Retain the handle so Windows PowerShell can read ExitCode after exit.
        $timeoutMs = if ($isLive) { 180000 } else { 90000 }
        if (-not $testProcess.WaitForExit($timeoutMs)) {
            & "$env:SystemRoot\System32\taskkill.exe" /PID $testProcess.Id /T /F | Out-Null
            throw "Desktop acceptance $run timed out; its own test process tree was stopped."
        }
        $testProcess.WaitForExit()
        if (-not (Test-Path -LiteralPath $receiptPath)) { throw "Desktop acceptance $run produced no receipt." }
        $receipt = [IO.File]::ReadAllText($receiptPath) | ConvertFrom-Json
        if ($testProcess.ExitCode -ne 0 -or $receipt.status -ne 'PASS') {
            throw "Desktop acceptance $run failed (exit=$($testProcess.ExitCode), receipt=$($receipt.status)): $($receipt.errors -join '; ')"
        }
        if ($PackagedExe -and (-not $receipt.packaged -or $receipt.executable_sha256 -ne $report.executable_sha256)) {
            throw "Desktop acceptance $run did not exercise the selected executable."
        }
        $report.checks += @{ name = $runName; status = 'PASS'; checks = $receipt.checks.Count; receipt = $receiptPath }
    }
    for ($run = 1; $run -le $Repeat; $run++) {
        $runName = "startup-{0:00}" -f $run
        $runOutput = Join-Path $outputRoot $runName
        $receiptPath = Join-Path $runOutput 'receipt.json'
        $program = if ($PackagedExe) { $PackagedExe } else { $python }
        $arguments = $(if ($PackagedExe) { '' } else { '-m codex_companion ' }) + '--startup-test --output "' + $runOutput + '"'
        if ($run % 2 -eq 0) { $arguments += ' --initial-settings' }
        Write-Host "$runName normal launch and native window visibility..."
        $testProcess = Start-Process -FilePath $program -ArgumentList $arguments -WorkingDirectory $repo -WindowStyle Hidden -PassThru
        $null = $testProcess.Handle
        if (-not $testProcess.WaitForExit(45000)) {
            & "$env:SystemRoot\System32\taskkill.exe" /PID $testProcess.Id /T /F | Out-Null
            throw "Startup acceptance $run timed out."
        }
        $testProcess.WaitForExit()
        if (-not (Test-Path -LiteralPath $receiptPath)) { throw "Startup acceptance $run produced no receipt." }
        $receipt = [IO.File]::ReadAllText($receiptPath) | ConvertFrom-Json
        if ($testProcess.ExitCode -ne 0 -or $receipt.status -ne 'PASS') {
            throw "Startup acceptance $run failed (exit=$($testProcess.ExitCode)): $($receipt.errors -join '; ')"
        }
        if ($PackagedExe -and (-not $receipt.packaged -or $receipt.executable_sha256 -ne $report.executable_sha256)) {
            throw "Startup acceptance $run did not exercise the selected executable."
        }
        $report.checks += @{ name = $runName; status = 'PASS'; checks = $receipt.checks.Count; receipt = $receiptPath }
    }
    $report.status = 'PASS'
    $resultCode = 0
} catch {
    $report.errors += $_.Exception.Message
    Write-Host $_.Exception.Message -ForegroundColor Red
} finally {
    Pop-Location
    $report.finished_at = [DateTime]::UtcNow.ToString('o')
    $reportPath = Join-Path $outputRoot 'receipt.json'
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    Write-Host "$($report.status): $reportPath"
}
exit $resultCode
