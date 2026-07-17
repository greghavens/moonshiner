# Regression harness for stallrecap.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_stallrecap.ps1
# Policy check included: every script in this repo declares
# Set-StrictMode -Version Latest and must run clean under it.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'stallrecap.ps1') -PathType Leaf)) {
    Write-Output 'FAIL stallrecap.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'stallrecap.ps1') @CaseArgs 1>$outFile 2>$errFile
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

$salesCsv = @'
stall,item,qty,unit,kind
birch,jam,3,4,sale
birch,loaf,10,3,sale
fern,soap,2,5,sale
birch,jam,1,4,refund
moss,eggs,12,2,sale
moss,herbs,4,3,sale
fern,soap,1,5,refund
'@

$stallsJson = @'
[
  {"id": "birch", "owner": "Ada Quist", "note": "cash only"},
  {"id": "fern", "owner": "Bo Reyes"},
  {"id": "moss", "owner": "Cy Uhl", "note": "corner pitch"},
  {"id": "reed", "owner": "Di Voss"}
]
'@

$dayExpected = @'
market day recap (4 stalls)
== stall birch (owner Ada Quist) ==
  note: cash only
  items sold: 13
  take: 42
  bulk lines: 1
  refunds: 1
== stall fern (owner Bo Reyes) ==
  items sold: 2
  take: 10
  bulk lines: 0
  refunds: 1
== stall moss (owner Cy Uhl) ==
  note: corner pitch
  items sold: 16
  take: 36
  bulk lines: 1
  refunds: 0
== stall reed (owner Di Voss) ==
  items sold: 0
  take: 0
  bulk lines: 0
  refunds: 0
day take: 88
refund rows: 2

'@

$loneSales = @'
stall,item,qty,unit,kind
alder,cheese,10,6,sale
'@

$loneStalls = @'
[
  {"id": "alder", "owner": "Edda Frost", "note": "new this season"}
]
'@

$loneExpected = @'
market day recap (1 stalls)
== stall alder (owner Edda Frost) ==
  note: new this season
  items sold: 10
  take: 60
  bulk lines: 1
  refunds: 0
day take: 60
refund rows: 0

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- lint policy: strict mode stays declared in the script ---
    $src = [System.IO.File]::ReadAllText((Join-Path $PSScriptRoot 'stallrecap.ps1'))
    Assert-True 'policy: Set-StrictMode -Version Latest declared' ($src.Contains('Set-StrictMode -Version Latest'))

    # --- full market day: optional notes, empty stalls, refunds ---
    $sales = Write-Fixture 'sales.csv' $salesCsv
    $stalls = Write-Fixture 'stalls.json' $stallsJson
    Invoke-Tool @('-Sales', $sales, '-Stalls', $stalls)
    Assert-True 'day: exit 0' ($RC -eq 0)
    Assert-Eq 'day: stderr empty' '' $ERR
    Assert-Eq 'day: recap' $dayExpected $OUT

    # --- single stall, single bulk sale row ---
    $ls = Write-Fixture 'lone_sales.csv' $loneSales
    $lt = Write-Fixture 'lone_stalls.json' $loneStalls
    Invoke-Tool @('-Sales', $ls, '-Stalls', $lt)
    Assert-True 'lone: exit 0' ($RC -eq 0)
    Assert-Eq 'lone: stderr empty' '' $ERR
    Assert-Eq 'lone: recap' $loneExpected $OUT

    # --- missing inputs ---
    $gone = Join-Path $T 'gone.csv'
    Invoke-Tool @('-Sales', $gone, '-Stalls', $stalls)
    Assert-True 'nosales: exit 66' ($RC -eq 66)
    Assert-Eq 'nosales: stdout empty' '' $OUT
    Assert-Eq 'nosales: message' "stallrecap: sales file not found: $gone`n" $ERR

    $gonej = Join-Path $T 'gone.json'
    Invoke-Tool @('-Sales', $sales, '-Stalls', $gonej)
    Assert-True 'nostalls: exit 66' ($RC -eq 66)
    Assert-Eq 'nostalls: stdout empty' '' $OUT
    Assert-Eq 'nostalls: message' "stallrecap: stall registry not found: $gonej`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
