# Acceptance harness for the OpsKit module.
# Run from the workspace root:  pwsh -NoProfile -File test_opskit.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$manifest = Join-Path $PSScriptRoot 'OpsKit' 'OpsKit.psd1'
if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
    Write-Output 'FAIL OpsKit/OpsKit.psd1 not found -- the module has not been built yet'
    exit 1
}

$T = Join-Path $PSScriptRoot '_t'
$FX = Join-Path $PSScriptRoot 'fixtures'
$desired = Join-Path $FX 'desired.json'
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

function Assert-Contains {
    param([string]$Label, [string]$Needle, [string]$Haystack)
    $script:checks++
    if ($Haystack.Contains($Needle)) { return }
    $script:fails++
    Write-Output "FAIL $Label"
    Write-Output '--- expected to contain ---'
    Write-Output $Needle
    Write-Output '--- actual ---'
    Write-Output $Haystack
    Write-Output '----------------'
}

$script:RC = 0
$script:OUT = ''
$script:ERR = ''
function Invoke-Driver {
    param([string]$Name, [string[]]$CaseArgs = @())
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    & pwsh -NoProfile -NonInteractive -File (Join-Path $T $Name) @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function Write-Driver {
    param([string]$Name, [string]$Body)
    [System.IO.File]::WriteAllText((Join-Path $T $Name), $Body)
}

function Get-DirSnapshot {
    param([string]$Dir)
    $names = @(Get-ChildItem -LiteralPath $Dir -File | ForEach-Object { $_.Name })
    [Array]::Sort($names, [System.StringComparer]::Ordinal)
    $parts = foreach ($n in $names) {
        $bytes = [System.IO.File]::ReadAllBytes((Join-Path $Dir $n))
        "$n=$([System.Convert]::ToBase64String($bytes))"
    }
    return ($parts -join ';')
}

# --- driver scripts (written into _t, run as child pwsh processes) ---

$drvApi = @'
param([string]$Module)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module $Module
$m = Test-ModuleManifest -Path $Module -WarningAction SilentlyContinue 3>$null
Write-Output "name=$($m.Name)"
$names = @((Get-Command -Module OpsKit).Name)
[Array]::Sort($names, [System.StringComparer]::Ordinal)
foreach ($n in $names) { Write-Output $n }
'@

$drvInv = @'
param([string]$Module, [string]$HostsDir)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module $Module
foreach ($h in @(Import-OpsInventory -Path $HostsDir)) {
    $pairs = @()
    foreach ($p in $h.Settings.PSObject.Properties) { $pairs += "$($p.Name)=$($p.Value)" }
    [Array]::Sort($pairs, [System.StringComparer]::Ordinal)
    Write-Output "$($h.Host)|$($h.Role)|$($pairs -join ',')"
}
'@

$drvDriftPipe = @'
param([string]$Module, [string]$HostsDir, [string]$Desired)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module $Module
Import-OpsInventory -Path $HostsDir | Compare-OpsState -Desired $Desired | ForEach-Object {
    Write-Output "$($_.Host) $($_.Setting) $($_.Kind) expected=$($_.Expected) actual=$($_.Actual)"
}
'@

$drvDriftParam = @'
param([string]$Module, [string]$HostsDir, [string]$Desired)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module $Module
$inv = @(Import-OpsInventory -Path $HostsDir)
foreach ($d in @(Compare-OpsState -Inventory $inv -Desired $Desired)) {
    Write-Output "$($d.Host) $($d.Setting) $($d.Kind) expected=$($d.Expected) actual=$($d.Actual)"
}
'@

$drvPlanPipe = @'
param([string]$Module, [string]$HostsDir, [string]$Desired)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module $Module
Import-OpsInventory -Path $HostsDir | Compare-OpsState -Desired $Desired | New-OpsPlan | ForEach-Object {
    Write-Output "$($_.Order) $($_.Action) $($_.Host).$($_.Setting) value=$($_.Value)"
}
'@

$drvPlanParam = @'
param([string]$Module, [string]$HostsDir, [string]$Desired)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module $Module
$inv = @(Import-OpsInventory -Path $HostsDir)
$drift = @(Compare-OpsState -Inventory $inv -Desired $Desired)
foreach ($s in @(New-OpsPlan -Drift $drift)) {
    Write-Output "$($s.Order) $($s.Action) $($s.Host).$($s.Setting) value=$($s.Value)"
}
'@

$drvClean = @'
param([string]$Module, [string]$CleanDir, [string]$Desired)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module $Module
$drift = @(Import-OpsInventory -Path $CleanDir | Compare-OpsState -Desired $Desired)
Write-Output "drift-count=$($drift.Count)"
$plan = @($drift | New-OpsPlan)
Write-Output "plan-count=$($plan.Count)"
'@

$drvImportOnly = @'
param([string]$Module, [string]$Dir)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module $Module
Import-OpsInventory -Path $Dir | Out-Null
'@

$drvCompareOnly = @'
param([string]$Module, [string]$Dir, [string]$Desired)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module $Module
Import-OpsInventory -Path $Dir | Compare-OpsState -Desired $Desired | Out-Null
'@

$drvApply = @'
param([string]$Module, [string]$StateDir, [string]$Desired)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module $Module
$plan = @(Import-OpsInventory -Path $StateDir | Compare-OpsState -Desired $Desired | New-OpsPlan)
foreach ($r in @(Invoke-OpsPlan -Plan $plan -Root $StateDir)) {
    Write-Output "$($r.Order) $($r.Host) $($r.Action) $($r.Setting) $($r.Status)"
}
$after = @(Import-OpsInventory -Path $StateDir | Compare-OpsState -Desired $Desired)
Write-Output "drift-after=$($after.Count)"
'@

$drvWhatIf = @'
param([string]$Module, [string]$StateDir, [string]$Desired)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module $Module
$plan = @(Import-OpsInventory -Path $StateDir | Compare-OpsState -Desired $Desired | New-OpsPlan)
Invoke-OpsPlan -Plan $plan -Root $StateDir -WhatIf | Out-Null
'@

$drvMissingState = @'
param([string]$Module, [string]$HostsDir, [string]$Desired, [string]$StateDir)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module $Module
$plan = @(Import-OpsInventory -Path $HostsDir | Compare-OpsState -Desired $Desired | New-OpsPlan)
Invoke-OpsPlan -Plan $plan -Root $StateDir | Out-Null
'@

# --- expected pins ---

$invExpected = @'
cache01|cache|tls=1.2
db01|db|pagecache=1024,tls=1.3
db02|db|pagecache=2048,tls=1.2,tmpdir=scratch
web01|web|banner=off,maxconn=512,tls=1.2
web02|web|maxconn=512,tls=1.3
web03|web|banner=off,debug=on,maxconn=512,tls=1.3
'@ + "`n"

$driftExpected = @'
cache01 evict missing expected=lru actual=
db01 pagecache value expected=2048 actual=1024
db02 tls value expected=1.3 actual=1.2
db02 tmpdir extra expected= actual=scratch
web01 tls value expected=1.3 actual=1.2
web02 banner missing expected=off actual=
web03 debug extra expected= actual=on
'@ + "`n"

$planExpected = @'
1 set cache01.evict value=lru
2 set db01.pagecache value=2048
3 set db02.tls value=1.3
4 set web01.tls value=1.3
5 set web02.banner value=off
6 clear db02.tmpdir value=
7 clear web03.debug value=
'@ + "`n"

$applyExpected = @'
1 cache01 set evict applied
2 db01 set pagecache applied
3 db02 set tls applied
4 web01 set tls applied
5 web02 set banner applied
6 db02 clear tmpdir applied
7 web03 clear debug applied
drift-after=0
'@ + "`n"

$apiExpected = @'
name=OpsKit
Compare-OpsState
Import-OpsInventory
Invoke-OpsPlan
New-OpsPlan
'@ + "`n"

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    Write-Driver 'drv_api.ps1' $drvApi
    Write-Driver 'drv_inv.ps1' $drvInv
    Write-Driver 'drv_drift_pipe.ps1' $drvDriftPipe
    Write-Driver 'drv_drift_param.ps1' $drvDriftParam
    Write-Driver 'drv_plan_pipe.ps1' $drvPlanPipe
    Write-Driver 'drv_plan_param.ps1' $drvPlanParam
    Write-Driver 'drv_clean.ps1' $drvClean
    Write-Driver 'drv_import_only.ps1' $drvImportOnly
    Write-Driver 'drv_compare_only.ps1' $drvCompareOnly
    Write-Driver 'drv_apply.ps1' $drvApply
    Write-Driver 'drv_whatif.ps1' $drvWhatIf
    Write-Driver 'drv_missing_state.ps1' $drvMissingState

    $hostsDir = Join-Path $FX 'hosts'

    # --- manifest + export discipline: exactly the four public commands ---
    Invoke-Driver 'drv_api.ps1' @($manifest)
    Assert-True 'api: exit 0' ($RC -eq 0)
    Assert-Eq 'api: manifest name and exported command list' $apiExpected $OUT

    # --- inventory import: ordinal host order, property-bag settings ---
    Invoke-Driver 'drv_inv.ps1' @($manifest, $hostsDir)
    Assert-True 'inventory: exit 0' ($RC -eq 0)
    Assert-Eq 'inventory: stderr empty' '' $ERR
    Assert-Eq 'inventory: one line per host, host-ordinal order' $invExpected $OUT

    # --- drift: pipeline binding and -Inventory produce identical reports ---
    Invoke-Driver 'drv_drift_pipe.ps1' @($manifest, $hostsDir, $desired)
    Assert-True 'drift(pipeline): exit 0' ($RC -eq 0)
    Assert-Eq 'drift(pipeline): report' $driftExpected $OUT

    Invoke-Driver 'drv_drift_param.ps1' @($manifest, $hostsDir, $desired)
    Assert-True 'drift(-Inventory): exit 0' ($RC -eq 0)
    Assert-Eq 'drift(-Inventory): report matches pipeline form' $driftExpected $OUT

    # --- plan: sets before clears, host/setting ordinal within each group ---
    Invoke-Driver 'drv_plan_pipe.ps1' @($manifest, $hostsDir, $desired)
    Assert-True 'plan(pipeline): exit 0' ($RC -eq 0)
    Assert-Eq 'plan(pipeline): ordered steps' $planExpected $OUT

    Invoke-Driver 'drv_plan_param.ps1' @($manifest, $hostsDir, $desired)
    Assert-True 'plan(-Drift): exit 0' ($RC -eq 0)
    Assert-Eq 'plan(-Drift): matches pipeline form' $planExpected $OUT

    # --- clean fleet: no drift, no plan ---
    Invoke-Driver 'drv_clean.ps1' @($manifest, (Join-Path $FX 'hosts_clean'), $desired)
    Assert-True 'clean: exit 0' ($RC -eq 0)
    Assert-Eq 'clean: zero drift, zero steps' ("drift-count=0`nplan-count=0`n") $OUT

    # --- error contracts ---
    Invoke-Driver 'drv_import_only.ps1' @($manifest, (Join-Path $FX 'hosts_dupe'))
    Assert-True 'dupe: nonzero exit' ($RC -ne 0)
    Assert-Eq 'dupe: stdout empty' '' $OUT
    Assert-Contains 'dupe: message' "OpsKit: duplicate host 'edge01'" $ERR

    Invoke-Driver 'drv_import_only.ps1' @($manifest, (Join-Path $FX 'hosts_bad'))
    Assert-True 'bad file: nonzero exit' ($RC -ne 0)
    Assert-Contains 'bad file: message' "OpsKit: invalid inventory file 'omega.json'" $ERR

    Invoke-Driver 'drv_compare_only.ps1' @($manifest, (Join-Path $FX 'hosts_badrole'), $desired)
    Assert-True 'unknown role: nonzero exit' ($RC -ne 0)
    Assert-Contains 'unknown role: message' "OpsKit: no desired state for role 'relay'" $ERR

    # --- apply: state files converge to desired, results in plan order ---
    $state1 = Join-Path $T 'state1'
    Copy-Item -LiteralPath $hostsDir -Destination $state1 -Recurse
    Invoke-Driver 'drv_apply.ps1' @($manifest, $state1, $desired)
    Assert-True 'apply: exit 0' ($RC -eq 0)
    Assert-Eq 'apply: stderr empty' '' $ERR
    Assert-Eq 'apply: step results then converged drift' $applyExpected $OUT

    # --- -WhatIf: nothing on disk moves ---
    $state2 = Join-Path $T 'state2'
    Copy-Item -LiteralPath $hostsDir -Destination $state2 -Recurse
    $before = Get-DirSnapshot $state2
    Invoke-Driver 'drv_whatif.ps1' @($manifest, $state2, $desired)
    Assert-True 'whatif: exit 0' ($RC -eq 0)
    Assert-Eq 'whatif: stderr empty' '' $ERR
    Assert-Eq 'whatif: state files byte-identical' $before (Get-DirSnapshot $state2)

    # --- missing state file: validated up front, nothing modified ---
    $state3 = Join-Path $T 'state3'
    Copy-Item -LiteralPath $hostsDir -Destination $state3 -Recurse
    Remove-Item -LiteralPath (Join-Path $state3 'web01.json')
    $before3 = Get-DirSnapshot $state3
    Invoke-Driver 'drv_missing_state.ps1' @($manifest, $hostsDir, $desired, $state3)
    Assert-True 'missing state: nonzero exit' ($RC -ne 0)
    Assert-Contains 'missing state: message' "OpsKit: no state file for host 'web01'" $ERR
    Assert-Eq 'missing state: remaining files untouched' $before3 (Get-DirSnapshot $state3)
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
