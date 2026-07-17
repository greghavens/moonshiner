# Regression harness for lagwatch.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_lagwatch.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'lagwatch.ps1') -PathType Leaf)) {
    Write-Output 'FAIL lagwatch.ps1 not found in the workspace root'
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

$script:RC = 0
$script:OUT = ''
$script:ERR = ''
function Invoke-Tool {
    param([string[]]$CaseArgs = @())
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'lagwatch.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function Write-Fixture {
    param([string]$Name, [string]$Content)
    $p = Join-Path $T $Name
    [System.IO.File]::WriteAllText($p, $Content)
    return $p
}

$mainCsv = @'
replica,lag_seconds
replica-a,45
replica-b,1200
replica-c,9
replica-d,310
replica-e,070
'@

$mainExpected = @'
lag report (limit 300s)
!! replica-b lag 1200s
!! replica-d lag 310s
ok replica-e lag 70s
ok replica-a lag 45s
ok replica-c lag 9s
alerts: 2

'@

$healthyCsv = @'
replica,lag_seconds
replica-x,4
replica-y,31
'@

$healthyExpected = @'
lag report (limit 300s)
ok replica-y lag 31s
ok replica-x lag 4s
alerts: 0

'@

$tightCsv = @'
replica,lag_seconds
replica-p,59
replica-q,61
replica-r,600
'@

$tightExpected = @'
lag report (limit 60s)
!! replica-r lag 600s
!! replica-q lag 61s
ok replica-p lag 59s
alerts: 2

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- worst-first report: only replicas at or over the limit alert ---
    $main = Write-Fixture 'lag.csv' $mainCsv
    Invoke-Tool @('-Path', $main, '-LimitSeconds', '300')
    Assert-True 'main: exit 65' ($RC -eq 65)
    Assert-Eq 'main: stderr empty' '' $ERR
    Assert-Eq 'main: report' $mainExpected $OUT

    # --- a healthy fleet never pages (default limit 300) ---
    $healthy = Write-Fixture 'healthy.csv' $healthyCsv
    Invoke-Tool @('-Path', $healthy)
    Assert-True 'healthy: exit 0' ($RC -eq 0)
    Assert-Eq 'healthy: stderr empty' '' $ERR
    Assert-Eq 'healthy: report' $healthyExpected $OUT

    # --- values straddling a tight limit land on the right side ---
    $tight = Write-Fixture 'tight.csv' $tightCsv
    Invoke-Tool @('-Path', $tight, '-LimitSeconds', '60')
    Assert-True 'tight: exit 65' ($RC -eq 65)
    Assert-Eq 'tight: report' $tightExpected $OUT

    # --- missing snapshot ---
    $gone = Join-Path $T 'gone.csv'
    Invoke-Tool @('-Path', $gone)
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "lagwatch: snapshot not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
