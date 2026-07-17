# Regression harness for netroster.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_netroster.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'netroster.ps1') -PathType Leaf)) {
    Write-Output 'FAIL netroster.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'netroster.ps1') @CaseArgs 1>$outFile 2>$errFile
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

$mainCsv = @'
name,site,rack
AP-10,west,r2
ap-2,east,r1
AP-7,west,r2
SW-3,core,r1
sw-11,core,r1
Cam-4,east,r3
cam-12,west,r3
'@

$mainExpected = @'
device roster
AP-10       west    r2
AP-7        west    r2
Cam-4       east    r3
SW-3        core    r1
ap-2        east    r1
cam-12      west    r3
sw-11       core    r1
devices: 7

'@

$relayCsv = @'
name,site,rack
RTR-1,north,r4
bridge-9,south,r5
BRIDGE-2,north,r4
rtr-4,south,r5
'@

$relayExpected = @'
device roster
BRIDGE-2    north   r4
RTR-1       north   r4
bridge-9    south   r5
rtr-4       south   r5
devices: 4

'@

$emptyExpected = @'
device roster
devices: 0

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- the roster follows the export tool's documented byte order: the
    # --- uppercase names form one block ahead of the lowercase block ---
    $main = Write-Fixture 'devices.csv' $mainCsv
    Invoke-Tool @('-Path', $main)
    Assert-True 'main: exit 0' ($RC -eq 0)
    Assert-Eq 'main: stderr empty' '' $ERR
    Assert-Eq 'main: roster' $mainExpected $OUT

    # --- a second inventory keeps the same ordering discipline ---
    $relay = Write-Fixture 'relay.csv' $relayCsv
    Invoke-Tool @('-Path', $relay)
    Assert-True 'relay: exit 0' ($RC -eq 0)
    Assert-Eq 'relay: roster' $relayExpected $OUT

    # --- a header-only inventory renders an empty roster ---
    $none = Write-Fixture 'none.csv' "name,site,rack`n"
    Invoke-Tool @('-Path', $none)
    Assert-True 'empty: exit 0' ($RC -eq 0)
    Assert-Eq 'empty: roster' $emptyExpected $OUT

    # --- missing inventory file ---
    $gone = Join-Path $T 'gone.csv'
    Invoke-Tool @('-Path', $gone)
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "netroster: inventory not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
