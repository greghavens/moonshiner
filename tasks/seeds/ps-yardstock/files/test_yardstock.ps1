# Regression harness for yardstock.ps1 and its lib/ tree.
# Run from the workspace root:  pwsh -NoProfile -File test_yardstock.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'yardstock.ps1') -PathType Leaf)) {
    Write-Output 'FAIL yardstock.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'yardstock.ps1') @CaseArgs 1>$outFile 2>$errFile
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

$stockCsv = @'
sku,kind,count,bay
OAK-2x4,plank,40,B2
PINE-1x6,plank,8,A1
BIRCH-DWL,dowel,15,C3
CEDAR-4x4,post,36,B1
FIR-2x2,strip,11,A2
'@

$defaultExpected = @'
OAK-2x4 [plank] bay B2: 40 -> ok
PINE-1x6 [plank] bay A1: 8 -> reorder
BIRCH-DWL [dowel] bay C3: 15 -> watch
CEDAR-4x4 [post] bay B1: 36 -> ok
FIR-2x2 [strip] bay A2: 11 -> reorder
reorder now: 2 of 5 lines

'@

$highExpected = @'
OAK-2x4 [plank] bay B2: 40 -> watch
PINE-1x6 [plank] bay A1: 8 -> reorder
BIRCH-DWL [dowel] bay C3: 15 -> reorder
CEDAR-4x4 [post] bay B1: 36 -> reorder
FIR-2x2 [strip] bay A2: 11 -> reorder
reorder now: 4 of 5 lines

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- default threshold: reorder under 12, watch under 36 ---
    $stock = Write-Fixture 'stock.csv' $stockCsv
    Invoke-Tool @('-Path', $stock)
    Assert-True 'default: exit 0' ($RC -eq 0)
    Assert-Eq 'default: stderr empty' '' $ERR
    Assert-Eq 'default: report' $defaultExpected $OUT

    # --- explicit threshold 40 ---
    Invoke-Tool @('-Path', $stock, '-Threshold', '40')
    Assert-True 'high: exit 0' ($RC -eq 0)
    Assert-Eq 'high: stderr empty' '' $ERR
    Assert-Eq 'high: report' $highExpected $OUT

    # --- threshold must bind as an integer ---
    Invoke-Tool @('-Path', $stock, '-Threshold', 'lots')
    Assert-True 'badthreshold: nonzero exit' ($RC -ne 0)
    Assert-Eq 'badthreshold: stdout empty' '' $OUT

    # --- missing ledger ---
    $gone = Join-Path $T 'gone.csv'
    Invoke-Tool @('-Path', $gone)
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "yardstock: stock file not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
