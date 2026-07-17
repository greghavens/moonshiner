# Acceptance harness for the Format-NodeCard parameter modernization.
# Run from the workspace root:  pwsh -NoProfile -File test_nodecard.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$lib = Join-Path $PSScriptRoot 'nodecard.ps1'
if (-not (Test-Path -LiteralPath $lib -PathType Leaf)) {
    Write-Output 'FAIL nodecard.ps1 not found in the workspace root'
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

    $null = Write-Fixture 'nodes.csv' @'
host,site,role,ip,rack
WEB01,Lyon,edge,10.4.2.7,r12
db01,lyon,db,10.4.2.20,r12
batch03,oslo,batch,10.7.0.4,r13
'@

    # ---------------------------------------------------------------
    # Compat pins: every invocation the runbooks use today must keep
    # producing exactly the same cards.
    # ---------------------------------------------------------------

    $compat = Write-Fixture 'driver_compat.ps1' @'
param([string]$Root, [string]$Fix)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $Root 'nodecard.ps1')
$doc = ''
$doc += "full=$(Format-NodeCard web01 lyon edge 10.4.2.7)`n"
$doc += "two=$(Format-NodeCard WEB02 LYON)`n"
$doc += "one=$(Format-NodeCard db01)`n"
$doc += "three=$(Format-NodeCard stats01 oslo batch)`n"
try {
    $null = Format-NodeCard
    $doc += "noargs=NONE`n"
} catch {
    $doc += "noargs=CAUGHT`n"
}
try {
    $null = Format-NodeCard a b c d e
    $doc += "fiveargs=NONE`n"
} catch {
    $doc += "fiveargs=CAUGHT`n"
}
$doc += "== sheet ==`n"
$doc += Format-RackSheet -Nodes @(Import-Csv -LiteralPath (Join-Path $Fix 'nodes.csv'))
[Console]::Out.Write($doc)
'@

    Invoke-File $compat @($PSScriptRoot, $T)
    Assert-True 'compat: driver exit 0' ($RC -eq 0)
    Assert-Eq 'compat: driver stderr empty' '' $ERR
    Assert-Eq 'compat: cards' @'
full=web01.lyon.grid.internal [edge] ip=10.4.2.7
two=web02.lyon.grid.internal [app]
one=db01.hq.grid.internal [app]
three=stats01.oslo.grid.internal [batch]
noargs=CAUGHT
fiveargs=CAUGHT
== sheet ==
web01.lyon.grid.internal [edge] ip=10.4.2.7
db01.lyon.grid.internal [db] ip=10.4.2.20
batch03.oslo.grid.internal [batch] ip=10.7.0.4

'@ $OUT

    # ---------------------------------------------------------------
    # The new surface: pipeline-by-property-name binding, named
    # parameters, and strict validation.
    # ---------------------------------------------------------------

    $modern = Write-Fixture 'driver_modern.ps1' @'
param([string]$Root, [string]$Fix)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $Root 'nodecard.ps1')
$doc = "== piped ==`n"
$cards = @(Import-Csv -LiteralPath (Join-Path $Fix 'nodes.csv') | Format-NodeCard)
$doc += (($cards -join "`n") + "`n")
$doc += "piped-count=$($cards.Count)`n"
$doc += "named=$(Format-NodeCard -Ip 10.1.2.3 -Name DB01)`n"
$doc += "mixed=$(Format-NodeCard STATS02 -Site BERN -Role db)`n"
try {
    $null = Format-NodeCard app01 hq toaster
    $doc += "badrole=NONE`n"
} catch {
    $doc += "badrole=CAUGHT`n"
}
try {
    $null = Format-NodeCard app01 hq app not-an-ip
    $doc += "badip=NONE`n"
} catch {
    $doc += "badip=CAUGHT`n"
}
try {
    $null = Format-NodeCard -Nmae app01
    $doc += "typo=NONE`n"
} catch {
    $doc += "typo=CAUGHT`n"
}
[Console]::Out.Write($doc)
'@

    Invoke-File $modern @($PSScriptRoot, $T)
    Assert-True 'modern: driver exit 0' ($RC -eq 0)
    Assert-Eq 'modern: driver stderr empty' '' $ERR
    Assert-Eq 'modern: binding and validation' @'
== piped ==
web01.lyon.grid.internal [edge] ip=10.4.2.7
db01.lyon.grid.internal [db] ip=10.4.2.20
batch03.oslo.grid.internal [batch] ip=10.7.0.4
piped-count=3
named=db01.hq.grid.internal [app] ip=10.1.2.3
mixed=stats02.bern.grid.internal [db]
badrole=CAUGHT
badip=CAUGHT
typo=CAUGHT

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
