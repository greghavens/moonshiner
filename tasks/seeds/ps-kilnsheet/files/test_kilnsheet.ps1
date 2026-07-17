# Regression harness for kilnsheet.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_kilnsheet.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'kilnsheet.ps1') -PathType Leaf)) {
    Write-Output 'FAIL kilnsheet.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'kilnsheet.ps1') @CaseArgs 1>$outFile 2>$errFile
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
kiln,item,pieces,cone
gasA,mugs,12,6
gasB,planters,4,10
gasA,bowls,7,6
electra,test tiles,20,04
'@

$dayExpected = @'
==============================
    studio firing sheet
==============================
window: morning
== kiln electra (soak 15 min, half shelves) ==
  test tiles x20 cone 04
  total pieces: 20
== kiln gasA (soak 20 min, tall shelves) ==
  mugs x12 cone 6
  bowls x7 cone 6
  total pieces: 19
== kiln gasB (soak 35 min, tall shelves) ==
  planters x4 cone 10
  total pieces: 4
------------------------------
 checked by: ______

'@

$loneCsv = @'
kiln,item,pieces,cone
gasB,urns,2,10
'@

$loneExpected = @'
==============================
    studio firing sheet
==============================
window: morning
== kiln gasB (soak 35 min, tall shelves) ==
  urns x2 cone 10
  total pieces: 2
------------------------------
 checked by: ______

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- full day, three kilns, ordinal kiln order ---
    $day = Write-Fixture 'firings.csv' $dayCsv
    Invoke-Tool @('-Path', $day)
    Assert-True 'day: exit 0' ($RC -eq 0)
    Assert-Eq 'day: stderr empty' '' $ERR
    Assert-Eq 'day: sheet' $dayExpected $OUT

    # --- single kiln, single row ---
    $lone = Write-Fixture 'lone.csv' $loneCsv
    Invoke-Tool @('-Path', $lone)
    Assert-True 'lone: exit 0' ($RC -eq 0)
    Assert-Eq 'lone: stderr empty' '' $ERR
    Assert-Eq 'lone: sheet' $loneExpected $OUT

    # --- missing firing log ---
    $gone = Join-Path $T 'gone.csv'
    Invoke-Tool @('-Path', $gone)
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "kilnsheet: firing log not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
