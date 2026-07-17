# Regression harness for quotaroll.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_quotaroll.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'quotaroll.ps1') -PathType Leaf)) {
    Write-Output 'FAIL quotaroll.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'quotaroll.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

$usageCsv = @'
user,team,used,extra
Baxter,QA,40,5
lindqvist,ops,300,20
baker,QA,12,0
alvarez,qa,7,2
Ochoa,QA,9,30
tran,ops,150,0
osei,QA,25,25
'@

# Team codes are exact directory strings: QA and qa are DIFFERENT teams.
# User names sort by strict ordinal (uppercase block before lowercase),
# and the per-user figure is used + extra as numbers.
$qaExpected = @'
quota roll: team QA
Baxter             45 GB
Ochoa              39 GB
baker              12 GB
osei               50 GB
team total: 146 GB
members: 4

'@

$lowerQaExpected = @'
quota roll: team qa
alvarez             9 GB
team total: 9 GB
members: 1

'@

$devExpected = @'
quota roll: team dev
team total: 0 GB
members: 0

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null
    $usage = Join-Path $T 'usage.csv'
    [System.IO.File]::WriteAllText($usage, $usageCsv + "`n")

    # --- the capacity-channel roll for team QA: exactly the four QA users,
    # --- ordinal user order, numeric totals ---
    Invoke-Tool @('-Path', $usage, '-Team', 'QA')
    Assert-True 'QA: exit 0' ($RC -eq 0)
    Assert-Eq 'QA: stderr empty' '' $ERR
    Assert-Eq 'QA: report' $qaExpected $OUT

    # --- the legacy lowercase team is its own roll ---
    Invoke-Tool @('-Path', $usage, '-Team', 'qa')
    Assert-True 'qa: exit 0' ($RC -eq 0)
    Assert-Eq 'qa: stderr empty' '' $ERR
    Assert-Eq 'qa: report' $lowerQaExpected $OUT

    # --- a team with no rows still renders a roll ---
    Invoke-Tool @('-Path', $usage, '-Team', 'dev')
    Assert-True 'dev: exit 0' ($RC -eq 0)
    Assert-Eq 'dev: report' $devExpected $OUT

    # --- missing export ---
    $gone = Join-Path $T 'gone.csv'
    Invoke-Tool @('-Path', $gone, '-Team', 'QA')
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "quotaroll: usage export not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
