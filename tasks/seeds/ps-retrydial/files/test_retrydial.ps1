# Acceptance harness for retrydial.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_retrydial.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$lib = Join-Path $PSScriptRoot 'retrydial.ps1'
if (-not (Test-Path -LiteralPath $lib -PathType Leaf)) {
    Write-Output 'FAIL retrydial.ps1 not found in the workspace root'
    exit 1
}

$T = Join-Path $PSScriptRoot '_t'
$script:checks = 0
$script:fails = 0

function Assert-Eq {
    param([string]$Label, [string]$Expected, [string]$Actual)
    $script:checks++
    if ($Expected -ceq $Actual) { return }
    $script:fails++
    Write-Output "FAIL $Label"
    Write-Output '--- expected ---'
    Write-Output $Expected
    Write-Output '--- actual ---'
    Write-Output $Actual
    Write-Output '----------------'
}

function Assert-True {
    param([string]$Label, [bool]$Condition)
    $script:checks++
    if ($Condition) { return }
    $script:fails++
    Write-Output "FAIL $Label"
}

# The whole scenario battery runs inside ONE child pwsh that dot-sources the
# library, drives it against the mock gateway with a recording sleep hook,
# and prints one block that is compared byte-exact.
$driver = @'
param([string]$BaseUrl)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'retrydial.ps1')

$script:sleeps = $null

function Invoke-Case {
    param([string]$Label, [string]$Path, [int]$MaxAttempts, [double]$BaseDelaySeconds)
    $script:sleeps = [System.Collections.Generic.List[double]]::new()
    $hook = { param($seconds) $script:sleeps.Add([double]$seconds) }
    try {
        $r = Invoke-ResilientRequest -Uri "$BaseUrl$Path" -MaxAttempts $MaxAttempts `
            -BaseDelaySeconds $BaseDelaySeconds -SleepHook $hook
        Write-Output "$Label=[status=$($r.Status) attempts=$($r.Attempts)] body=[$($r.Body)]"
    } catch {
        Write-Output "$Label=[ERR $($_.Exception.Message)]"
    }
    $inv = [System.Globalization.CultureInfo]::InvariantCulture
    $fmt = @($script:sleeps | ForEach-Object { $_.ToString('0.###', $inv) }) -join ','
    Write-Output "$Label.sleeps=[$fmt]"
}

Invoke-Case 'ok' '/ok' 3 1
Invoke-Case 'flaky' '/flaky' 5 0.5
Invoke-Case 'wall' '/wall' 4 0.25
Invoke-Case 'throttle' '/throttle' 3 60
Invoke-Case 'plain' '/throttle-plain' 4 2
Invoke-Case 'reject' '/reject' 5 1
Invoke-Case 'missing' '/missing' 5 1
'@

$expected = @'
ok=[status=200 attempts=1] body=[{"path": "/ok", "call": 1, "status": 200}]
ok.sleeps=[]
flaky=[status=200 attempts=3] body=[{"path": "/flaky", "call": 3, "status": 200}]
flaky.sleeps=[0.5,7]
wall=[ERR retrydial: gave up after 4 attempts (last status 500)]
wall.sleeps=[0.25,0.5,1]
throttle=[status=200 attempts=2] body=[{"path": "/throttle", "call": 2, "status": 200}]
throttle.sleeps=[3]
plain=[status=200 attempts=3] body=[{"path": "/throttle-plain", "call": 3, "status": 200}]
plain.sleeps=[2,4]
reject=[ERR retrydial: client error 400, not retrying]
reject.sleeps=[]
missing=[ERR retrydial: client error 404, not retrying]
missing.sleeps=[]

'@

$expectedLog = @'
/ok 200
/flaky 500
/flaky 503
/flaky 200
/wall 500
/wall 500
/wall 500
/wall 500
/throttle 429
/throttle 200
/throttle-plain 429
/throttle-plain 429
/throttle-plain 200
/reject 400
/missing 404
'@

$srv = $null
try {
    New-Item -ItemType Directory -Force -Path $T > $null
    $portFile = Join-Path $T 'port.txt'
    $srv = Start-Process -FilePath 'python3' `
        -ArgumentList @((Join-Path $PSScriptRoot 'mock_flaky.py'), $portFile) `
        -PassThru `
        -RedirectStandardOutput (Join-Path $T 'srv.out') `
        -RedirectStandardError (Join-Path $T 'srv.err')
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Path -LiteralPath $portFile)) {
        if ($srv.HasExited -or [DateTime]::UtcNow -gt $deadline) {
            throw "mock server failed to start: $(Get-Content -LiteralPath (Join-Path $T 'srv.err') -Raw -ErrorAction SilentlyContinue)"
        }
        Start-Sleep -Milliseconds 50
    }
    $port = [int](Get-Content -LiteralPath $portFile -Raw).Trim()
    $baseUrl = "http://127.0.0.1:$port"

    $driverPath = Join-Path $T 'driver.ps1'
    [System.IO.File]::WriteAllText($driverPath, $driver)
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & pwsh -NoProfile -NonInteractive -File $driverPath $baseUrl 1>$outFile 2>$errFile
    $rc = $LASTEXITCODE
    $sw.Stop()

    Assert-True 'driver exits 0' ($rc -eq 0)
    Assert-Eq 'driver stderr empty' '' ([System.IO.File]::ReadAllText($errFile))
    Assert-Eq 'scenario block' $expected ([System.IO.File]::ReadAllText($outFile))

    # the recorded schedule adds up to ~19s of nominal waiting — if the
    # library actually slept instead of calling the hook, this trips
    Assert-True 'sleep hook used (battery finished fast)' ($sw.Elapsed.TotalSeconds -lt 12)

    $entries = Invoke-RestMethod -Uri "$baseUrl/__log__"
    $logText = (@($entries | ForEach-Object { "$($_.path) $($_.status)" }) -join "`n")
    Assert-Eq 'gateway request log' $expectedLog $logText
} finally {
    if ($srv -and -not $srv.HasExited) { $srv.Kill(); $srv.WaitForExit() }
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
