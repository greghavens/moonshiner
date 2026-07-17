# Acceptance harness for the loadsheet object-passing rework.
# Run from the workspace root:  pwsh -NoProfile -File test_loadsheet.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$lib = Join-Path $PSScriptRoot 'loadsheet.ps1'
if (-not (Test-Path -LiteralPath $lib -PathType Leaf)) {
    Write-Output 'FAIL loadsheet.ps1 not found in the workspace root'
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
function Invoke-File {
    param([string]$Path, [string[]]$CaseArgs = @())
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    & pwsh -NoProfile -NonInteractive -File $Path @CaseArgs 1>$outFile 2>$errFile
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

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    $null = Write-Fixture 'loads.csv' @'
id,dest,kg,pri
LD-104,Smithers,920,2
LD-093,"Fort St. James",1480,1
LD-121,Terrace,310,3
LD-088,"Hundred Mile House",640,2
LD-140,Hazelton,205,1
'@

    $null = Write-Fixture 'bad_loads.csv' @'
id,dest,kg,pri
LD-201,Kitimat,700,2
LD-202,Stewart,heavy,1
'@

    # ---------------------------------------------------------------
    # Frozen behavior: the rendered sheet must not move by a byte, no
    # matter what the helpers pass among themselves.
    # ---------------------------------------------------------------

    $compat = Write-Fixture 'driver_compat.ps1' @'
param([string]$Root, [string]$Fix)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $Root 'loadsheet.ps1')
$rows = @(Read-LoadRows -Path (Join-Path $Fix 'loads.csv'))
$doc = "== main ==`n"
$doc += Format-LoadSheet -Rows $rows -Title 'northbound am'
$doc += "== heavy ==`n"
$doc += Format-LoadSheet -Rows (Select-HeavyLoads -Rows $rows -MinKg 500) -Title 'over 500 kg'
$doc += "== terrace ==`n"
$doc += Format-LoadSheet -Rows (Select-Destination -Rows $rows -Dest 'Terrace') -Title 'Terrace only'
$doc += "== empty ==`n"
$doc += Format-LoadSheet -Rows @(Select-HeavyLoads -Rows $rows -MinKg 9000) -Title 'empty run'
try {
    $null = Read-LoadRows -Path (Join-Path $Fix 'bad_loads.csv')
    $doc += "err=NONE`n"
} catch {
    $doc += "err=$($_.Exception.Message)`n"
}
[Console]::Out.Write($doc)
'@

    Invoke-File $compat @($PSScriptRoot, $T)
    Assert-True 'compat: driver exit 0' ($RC -eq 0)
    Assert-Eq 'compat: driver stderr empty' '' $ERR
    Assert-Eq 'compat: rendered sheets' @'
== main ==
LOAD SHEET: northbound am
id        dest                    kg  pri
LD-104    Smithers               920    2
LD-093    Fort St. James        1480    1
LD-121    Terrace                310    3
LD-088    Hundred Mile House     640    2
LD-140    Hazelton               205    1
total 5 loads, 3555 kg
== heavy ==
LOAD SHEET: over 500 kg
id        dest                    kg  pri
LD-104    Smithers               920    2
LD-093    Fort St. James        1480    1
LD-088    Hundred Mile House     640    2
total 3 loads, 3040 kg
== terrace ==
LOAD SHEET: Terrace only
id        dest                    kg  pri
LD-121    Terrace                310    3
total 1 loads, 310 kg
== empty ==
LOAD SHEET: empty run
no loads
err=bad_loads.csv: bad row 3

'@ $OUT

    # ---------------------------------------------------------------
    # The new surface: typed records, property access, pipeline flow.
    # ---------------------------------------------------------------

    $objects = Write-Fixture 'driver_objects.ps1' @'
param([string]$Root, [string]$Fix)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $Root 'loadsheet.ps1')
$rows = @(Read-LoadRows -Path (Join-Path $Fix 'loads.csv'))
$doc = "count=$($rows.Count)`n"
$doc += "id0=$($rows[0].Id)`n"
$doc += "kg-int=$($rows[1].Kg -is [int])`n"
$doc += "pri-int=$($rows[0].Pri -is [int])`n"
$doc += "dest1=$($rows[1].Dest)`n"
$heavyPipe = @($rows | Select-HeavyLoads -MinKg 500)
$doc += "heavy=$((@($heavyPipe | ForEach-Object Id) -join ','))`n"
$heavyArg = @(Select-HeavyLoads -Rows $rows -MinKg 500)
$same = (@($heavyPipe | ForEach-Object Id) -join ',') -ceq (@($heavyArg | ForEach-Object Id) -join ',')
$doc += "same=$same`n"
$chain = @($rows | Select-HeavyLoads -MinKg 500 | Select-Destination -Dest 'Fort St. James')
$doc += "chain=$($chain[0].Id)/$($chain[0].Kg)`n"
$m = $rows | Measure-Loads
$doc += "measure=$($m.Count)|$($m.TotalKg)|$($m.MaxKg)`n"
$doc += "total-int=$($m.TotalKg -is [int])`n"
$m0 = Measure-Loads -Rows @()
$doc += "zero=$($m0.Count)|$($m0.TotalKg)|$($m0.MaxKg)`n"
$sheetA = $rows | Format-LoadSheet -Title 'northbound am'
$sheetB = Format-LoadSheet -Rows $rows -Title 'northbound am'
$doc += "sheets-equal=$($sheetA -ceq $sheetB)`n"
$doc += $sheetA
[Console]::Out.Write($doc)
'@

    Invoke-File $objects @($PSScriptRoot, $T)
    Assert-True 'objects: driver exit 0' ($RC -eq 0)
    Assert-Eq 'objects: driver stderr empty' '' $ERR
    Assert-Eq 'objects: typed records and pipeline' @'
count=5
id0=LD-104
kg-int=True
pri-int=True
dest1=Fort St. James
heavy=LD-104,LD-093,LD-088
same=True
chain=LD-093/1480
measure=5|3555|1480
total-int=True
zero=0|0|0
sheets-equal=True
LOAD SHEET: northbound am
id        dest                    kg  pri
LD-104    Smithers               920    2
LD-093    Fort St. James        1480    1
LD-121    Terrace                310    3
LD-088    Hundred Mile House     640    2
LD-140    Hazelton               205    1
total 5 loads, 3555 kg

'@ $OUT
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
