# Acceptance harness for haulstats.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_haulstats.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$tool = Join-Path $PSScriptRoot 'haulstats.ps1'
if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
    Write-Output 'FAIL haulstats.ps1 not found in the workspace root'
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

# Aggregation goes through the object pipeline by team convention; the
# harness checks the idiom is actually used.
$src = [System.IO.File]::ReadAllText($tool).ToLowerInvariant()
Assert-True 'source uses Group-Object' ($src.Contains('group-object'))
Assert-True 'source uses Measure-Object' ($src.Contains('measure-object'))

$script:RC = 0
$script:OUT = ''
$script:ERR = ''
function Invoke-Tool {
    param([string[]]$CaseArgs = @())
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'haulstats.ps1') @CaseArgs 1>$outFile 2>$errFile
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

$mainExpected = @'
"Region","Carrier","Shipments","TotalKg","MinKg","MaxKg","AvgKg"
"APAC","apex","1","7.00","7.00","7.00","7.00"
"apac","apex","4","18.00","1.50","10.25","4.50"
"emea","Brightline","2","8.00","3.50","4.50","4.00"
"emea","apex","1","12.00","12.00","12.00","12.00"

'@

$smallExpected = @'
"Region","Carrier","Shipments","TotalKg","MinKg","MaxKg","AvgKg"
"na","apex","2","8.75","3.25","5.50","4.38"

'@

$headerOnly = @'
"Region","Carrier","Shipments","TotalKg","MinKg","MaxKg","AvgKg"

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- the dirty feed: case variants are DIFFERENT groups, and the group
    # --- order is ordinal (capitals first), region then carrier ---
    $main = Write-Fixture 'shipments.csv' "Region,Carrier,WeightKg`napac,apex,10.25`nemea,Brightline,4.5`napac,apex,4.25`nAPAC,apex,7`nemea,apex,12`nemea,Brightline,3.5`napac,apex,1.5`napac,apex,2`n"
    Invoke-Tool @('-Path', $main)
    Assert-True 'main: exit 0' ($RC -eq 0)
    Assert-Eq 'main: stderr empty' '' $ERR
    Assert-Eq 'main: report' $mainExpected $OUT

    # --- averages round half away from zero (8.75 / 2 = 4.375 -> 4.38) ---
    $small = Write-Fixture 'small.csv' "Region,Carrier,WeightKg`nna,apex,3.25`nna,apex,5.5`n"
    Invoke-Tool @('-Path', $small)
    Assert-True 'small: exit 0' ($RC -eq 0)
    Assert-Eq 'small: report' $smallExpected $OUT

    # --- a header-only feed still prints the report header ---
    $empty = Write-Fixture 'empty.csv' "Region,Carrier,WeightKg`n"
    Invoke-Tool @('-Path', $empty)
    Assert-True 'empty: exit 0' ($RC -eq 0)
    Assert-Eq 'empty: report' $headerOnly $OUT

    # --- a weight that does not parse stops the run ---
    $bad = Write-Fixture 'bad.csv' "Region,Carrier,WeightKg`nna,apex,4`nna,apex,heavy`n"
    Invoke-Tool @('-Path', $bad)
    Assert-True 'bad: exit 65' ($RC -eq 65)
    Assert-Eq 'bad: stdout empty' '' $OUT
    Assert-Eq 'bad: message' "haulstats: bad WeightKg 'heavy' at data row 2`n" $ERR

    # --- required columns are checked ---
    $nocol = Write-Fixture 'nocol.csv' "Region,Carrier,Kg`nna,apex,1`n"
    Invoke-Tool @('-Path', $nocol)
    Assert-True 'nocol: exit 65' ($RC -eq 65)
    Assert-Eq 'nocol: message' "haulstats: required column 'WeightKg' not found`n" $ERR

    # --- missing file ---
    $gone = Join-Path $T 'gone.csv'
    Invoke-Tool @('-Path', $gone)
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "haulstats: file not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
