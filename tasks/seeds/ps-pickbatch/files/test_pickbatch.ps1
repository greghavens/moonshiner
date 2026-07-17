# Regression harness for pickbatch.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_pickbatch.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'pickbatch.ps1') -PathType Leaf)) {
    Write-Output 'FAIL pickbatch.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'pickbatch.ps1') @CaseArgs 1>$outFile 2>$errFile
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

$mainExport = @'
[
  {"id": "ord-101", "status": "open",   "priority": "standard", "items": 3, "bay": "A2"},
  {"id": "ord-102", "status": "open",   "priority": "rush",     "items": 4, "bay": "B1"},
  {"id": "ord-103", "status": "packed", "priority": "rush",     "items": 2, "bay": "A5"},
  {"id": "ord-105", "status": "open",   "priority": "rush",     "items": 2, "bay": "C4"},
  {"id": "ord-107", "status": "open",   "priority": "standard", "items": 6, "bay": "B3"},
  {"id": "ord-109", "status": "open",   "priority": "rush",     "items": 6, "bay": "A1"},
  {"id": "ord-110", "status": "held",   "priority": "rush",     "items": 1, "bay": "C2"}
]
'@

$mainExpected = @'
rush orders: 3
 - ord-102  4 items  bay B1
 - ord-105  2 items  bay C4
 - ord-109  6 items  bay A1

'@

$singleExpected = @'
rush orders: 1
 - ord-201  5 items  bay D1

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- the pick list is exactly the open rush orders, in export order ---
    $main = Write-Fixture 'orders.json' $mainExport
    Invoke-Tool @('-Path', $main)
    Assert-True 'main: exit 0' ($RC -eq 0)
    Assert-Eq 'main: stderr empty' '' $ERR
    Assert-Eq 'main: pick list' $mainExpected $OUT

    # --- a day with no open rush orders prints a zero count and nothing else ---
    $quiet = Write-Fixture 'quiet.json' '[{"id": "ord-301", "status": "open", "priority": "standard", "items": 2, "bay": "A1"}, {"id": "ord-302", "status": "packed", "priority": "rush", "items": 3, "bay": "B2"}]'
    Invoke-Tool @('-Path', $quiet)
    Assert-True 'quiet: exit 0' ($RC -eq 0)
    Assert-Eq 'quiet: stderr empty' '' $ERR
    Assert-Eq 'quiet: pick list' "rush orders: 0`n" $OUT

    # --- a single rush order counts as one and lists once ---
    $single = Write-Fixture 'single.json' '[{"id": "ord-201", "status": "open", "priority": "rush", "items": 5, "bay": "D1"}]'
    Invoke-Tool @('-Path', $single)
    Assert-True 'single: exit 0' ($RC -eq 0)
    Assert-Eq 'single: stderr empty' '' $ERR
    Assert-Eq 'single: pick list' $singleExpected $OUT

    # --- missing export file ---
    $gone = Join-Path $T 'gone.json'
    Invoke-Tool @('-Path', $gone)
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "pickbatch: export not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
