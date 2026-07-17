# Regression harness for pitchcard.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_pitchcard.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'pitchcard.ps1') -PathType Leaf)) {
    Write-Output 'FAIL pitchcard.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'pitchcard.ps1') @CaseArgs 1>$outFile 2>$errFile
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

$dayCsv = @'
id,zone,party,nights
A4,river,4,3
B1,meadow,2,1
C7,orchard,6,2
'@

$standardExpected = @'
arrivals: 3 pitches (standard season)
-- pitch A4 zone river --
  party of 4, 3 nights
  fee: 36
  pitch A4 ready
-- pitch B1 zone meadow --
  party of 2, 1 nights
  fee: 12
  pitch B1 ready
-- pitch C7 zone orchard --
  party of 6, 2 nights
  fee: 24
  pitch C7 ready

'@

$peakExpected = @'
arrivals: 3 pitches (peak season)
-- pitch A4 zone river --
  party of 4, 3 nights
  fee: 45 (peak rate)
  pitch A4 ready
-- pitch B1 zone meadow --
  party of 2, 1 nights
  fee: 15 (peak rate)
  pitch B1 ready
-- pitch C7 zone orchard --
  party of 6, 2 nights
  fee: 30 (peak rate)
  pitch C7 ready

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- default season: standard fees, no surcharge lines ---
    $day = Write-Fixture 'pitches.csv' $dayCsv
    Invoke-Tool @('-Path', $day)
    Assert-True 'standard: exit 0' ($RC -eq 0)
    Assert-Eq 'standard: stderr empty' '' $ERR
    Assert-Eq 'standard: cards' $standardExpected $OUT

    # --- explicit peak season: surcharge applied per night ---
    Invoke-Tool @('-Path', $day, '-Season', 'peak')
    Assert-True 'peak: exit 0' ($RC -eq 0)
    Assert-Eq 'peak: stderr empty' '' $ERR
    Assert-Eq 'peak: cards' $peakExpected $OUT

    # --- a season outside the allowed set must be rejected up front ---
    Invoke-Tool @('-Path', $day, '-Season', 'winter')
    Assert-True 'badseason: nonzero exit' ($RC -ne 0)
    Assert-Eq 'badseason: stdout empty' '' $OUT

    # --- missing assignment list ---
    $gone = Join-Path $T 'gone.csv'
    Invoke-Tool @('-Path', $gone)
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "pitchcard: assignment list not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
