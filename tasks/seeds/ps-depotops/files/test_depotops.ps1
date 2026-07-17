# Acceptance harness for the DepotOps module split.
# Run from the workspace root:  pwsh -NoProfile -File test_depotops.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$cli = Join-Path $PSScriptRoot 'depotops.ps1'
if (-not (Test-Path -LiteralPath $cli -PathType Leaf)) {
    Write-Output 'FAIL depotops.ps1 not found in the workspace root'
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

    $stock = Write-Fixture 'stock.csv' @'
sku,desc,qty,min
VAL-220,"valve, 22mm",6,4
BRK-100,brake pad set,3,5
FLT-310,fuel filter,9,2
HOS-455,"hose, braided",0,1
CHN-770,drive chain,4,4
'@

    $orders = Write-Fixture 'orders.csv' @'
order,sku,qty
SO-9001,FLT-310,4
SO-9002,VAL-220,1
SO-9003,FLT-310,4
SO-9004,CHN-770,1
'@

    $auditStock = Write-Fixture 'audit_stock.csv' @'
sku,desc,qty,min
PMP-300,pump,-2,1
PMP-300,pump spare,4,1
SEA-410,seal kit,3,-1
LGT-150,lamp,7,2
'@

    $auditOrders = Write-Fixture 'audit_orders.csv' @'
order,sku,qty
SO-1,LGT-150,1
SO-2,GSK-990,2
SO-1,PMP-300,1
'@

    $badStock = Write-Fixture 'bad_stock.csv' @'
sku,desc,qty,min
NUT-010,lock nut,5,1
BLT-020,hex bolt,four,1
'@

    $unknownOrders = Write-Fixture 'orders_unknown.csv' @'
order,sku,qty
SO-1,ZAA-111,1
SO-2,AAA-222,1
'@

    $emptyStock = Write-Fixture 'empty_stock.csv' @'
sku,desc,qty,min
'@

    # ---------------------------------------------------------------
    # Frozen behavior: the CLI must keep producing exactly what the
    # monolith produces today, byte for byte.
    # ---------------------------------------------------------------

    Invoke-File $cli @('report', '-Stock', $stock)
    Assert-True 'cli report: exit 0' ($RC -eq 0)
    Assert-Eq 'cli report: stderr empty' '' $ERR
    Assert-Eq 'cli report: lines' @'
BRK-100 qty=3 min=5 LOW
CHN-770 qty=4 min=4 ok
FLT-310 qty=9 min=2 ok
HOS-455 qty=0 min=1 LOW
VAL-220 qty=6 min=4 ok

'@ $OUT

    Invoke-File $cli @('shortfall', '-Stock', $stock, '-Orders', $orders)
    Assert-True 'cli shortfall: exit 0' ($RC -eq 0)
    Assert-Eq 'cli shortfall: stderr empty' '' $ERR
    Assert-Eq 'cli shortfall: lines' @'
BRK-100 need=2
CHN-770 need=1
FLT-310 need=1
HOS-455 need=1

'@ $OUT

    Invoke-File $cli @('audit', '-Stock', $auditStock, '-Orders', $auditOrders)
    Assert-True 'cli audit: exit 65' ($RC -eq 65)
    Assert-Eq 'cli audit: stderr empty' '' $ERR
    Assert-Eq 'cli audit: findings' @'
dup-order SO-1
dup-sku PMP-300
neg-min SEA-410
neg-qty PMP-300
unknown-sku SO-2 GSK-990

'@ $OUT

    Invoke-File $cli @('audit', '-Stock', $stock, '-Orders', $orders)
    Assert-True 'cli audit clean: exit 0' ($RC -eq 0)
    Assert-Eq 'cli audit clean: no output' '' $OUT

    Invoke-File $cli @('report', '-Stock', $emptyStock)
    Assert-True 'cli empty report: exit 0' ($RC -eq 0)
    Assert-Eq 'cli empty report: no output' '' $OUT

    Invoke-File $cli @('report', '-Stock', $badStock)
    Assert-True 'cli bad row: exit 64' ($RC -eq 64)
    Assert-Eq 'cli bad row: stdout empty' '' $OUT
    Assert-Eq 'cli bad row: message' "depotops: bad_stock.csv: bad row 3`n" $ERR

    Invoke-File $cli @('shortfall', '-Stock', $stock, '-Orders', $unknownOrders)
    Assert-True 'cli unknown sku: exit 65' ($RC -eq 65)
    Assert-Eq 'cli unknown sku: stdout empty' '' $OUT
    Assert-Eq 'cli unknown sku: message' "depotops: unknown sku in orders: AAA-222`n" $ERR

    $missing = Join-Path $T 'nope.csv'
    Invoke-File $cli @('report', '-Stock', $missing)
    Assert-True 'cli missing file: exit 66' ($RC -eq 66)
    Assert-Eq 'cli missing file: message' "depotops: file not found: $missing`n" $ERR

    Invoke-File $cli @('polish', '-Stock', $stock)
    Assert-True 'cli unknown command: exit 64' ($RC -eq 64)
    Assert-Eq 'cli unknown command: message' "depotops: unknown command: polish`n" $ERR

    Invoke-File $cli @('report')
    Assert-True 'cli missing -Stock: exit 64' ($RC -eq 64)
    Assert-Eq 'cli missing -Stock: message' "depotops: -Stock is required`n" $ERR

    Invoke-File $cli @('shortfall', '-Stock', $stock)
    Assert-True 'cli missing -Orders: exit 64' ($RC -eq 64)
    Assert-Eq 'cli missing -Orders: message' "depotops: -Orders is required for shortfall`n" $ERR

    # ---------------------------------------------------------------
    # The new surface: DepotOps.psd1 manifest + public functions.
    # ---------------------------------------------------------------

    $driver = Write-Fixture 'driver_module.ps1' @'
param([string]$Root, [string]$Fix)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $Root 'DepotOps.psd1')
$m = Get-Module DepotOps
$names = @($m.ExportedFunctions.Keys)
[Array]::Sort($names, [System.StringComparer]::Ordinal)
Write-Output ('exports=' + ($names -join ','))
Write-Output ('cmd-count=' + @(Get-Command -Module DepotOps).Count)

$stock = @(Import-DepotStock -Path (Join-Path $Fix 'stock.csv'))
Write-Output ('stock-count=' + $stock.Count)
$brk = $stock | Where-Object { $_.Sku -ceq 'BRK-100' }
Write-Output ('brk-qty-int=' + ($brk.Qty -is [int]))
Write-Output ('brk-desc=' + $brk.Desc)
$val = $stock | Where-Object { $_.Sku -ceq 'VAL-220' }
Write-Output ('val-desc=' + $val.Desc)

$report = @(Get-DepotReport -Stock $stock)
Write-Output ('report-skus=' + (@($report | ForEach-Object Sku) -join ','))
Write-Output ('report-first=' + $report[0].Sku + '|' + $report[0].Qty + '|' + $report[0].Min + '|' + $report[0].Status)
$low = @($report | Where-Object { $_.Status -ceq 'LOW' })
Write-Output ('low=' + (@($low | ForEach-Object Sku) -join ','))

$orders = @(Import-DepotOrders -Path (Join-Path $Fix 'orders.csv'))
Write-Output ('orders-count=' + $orders.Count)
$short = @(Get-DepotShortfall -Stock $stock -Orders $orders)
Write-Output ('short=' + (@($short | ForEach-Object { $_.Sku + ':' + $_.Need }) -join ','))
Write-Output ('need-int=' + ($short[0].Need -is [int]))

$aStock = @(Import-DepotStock -Path (Join-Path $Fix 'audit_stock.csv'))
$aOrders = @(Import-DepotOrders -Path (Join-Path $Fix 'audit_orders.csv'))
$findings = @(Get-DepotFindings -Stock $aStock -Orders $aOrders)
Write-Output ('findings=' + (@($findings | ForEach-Object { $_.Kind + '/' + $_.Subject }) -join ';'))
$clean = @(Get-DepotFindings -Stock $stock -Orders $orders)
Write-Output ('clean-count=' + $clean.Count)

try {
    $null = Import-DepotStock -Path (Join-Path $Fix 'bad_stock.csv')
    Write-Output 'baderr=NONE'
} catch {
    Write-Output ('baderr=' + $_.Exception.Message)
}
try {
    $null = Get-DepotShortfall -Stock $stock -Orders (Import-DepotOrders -Path (Join-Path $Fix 'orders_unknown.csv'))
    Write-Output 'unkerr=NONE'
} catch {
    Write-Output ('unkerr=' + $_.Exception.Message)
}
'@

    Invoke-File $driver @($PSScriptRoot, $T)
    Assert-True 'module: driver exit 0' ($RC -eq 0)
    Assert-Eq 'module: driver stderr empty' '' $ERR
    Assert-Eq 'module: driver output' @'
exports=Get-DepotFindings,Get-DepotReport,Get-DepotShortfall,Import-DepotOrders,Import-DepotStock
cmd-count=5
stock-count=5
brk-qty-int=True
brk-desc=brake pad set
val-desc=valve, 22mm
report-skus=BRK-100,CHN-770,FLT-310,HOS-455,VAL-220
report-first=BRK-100|3|5|LOW
low=BRK-100,HOS-455
orders-count=4
short=BRK-100:2,CHN-770:1,FLT-310:1,HOS-455:1
need-int=True
findings=dup-order/SO-1;dup-sku/PMP-300;neg-min/SEA-410;neg-qty/PMP-300;unknown-sku/SO-2 GSK-990
clean-count=0
baderr=bad_stock.csv: bad row 3
unkerr=unknown sku in orders: AAA-222

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
