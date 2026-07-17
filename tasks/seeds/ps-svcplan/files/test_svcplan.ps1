# Acceptance harness for svcplan.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_svcplan.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$tool = Join-Path $PSScriptRoot 'svcplan.ps1'
if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
    Write-Output 'FAIL svcplan.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'svcplan.ps1') @CaseArgs 1>$outFile 2>$errFile
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

$mainPlan = @'
stop legacy
stop metrics
start cache
start ui
restart db
restart api
enable cache
disable legacy
disable metrics

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # desired is deliberately not in dependency or ordinal order
    $desired = Write-Fixture 'desired.json' @'
{
  "services": [
    { "name": "worker",  "state": "running", "enabled": true,  "configRev": 4, "dependsOn": ["api"] },
    { "name": "db",      "state": "running", "enabled": true,  "configRev": 3, "dependsOn": [] },
    { "name": "cache",   "state": "running", "enabled": true,  "configRev": 2, "dependsOn": [] },
    { "name": "api",     "state": "running", "enabled": true,  "configRev": 7, "dependsOn": ["cache", "db"] },
    { "name": "ui",      "state": "running", "enabled": true,  "configRev": 2, "dependsOn": ["api"] },
    { "name": "metrics", "state": "stopped", "enabled": false, "configRev": 1, "dependsOn": ["db"] },
    { "name": "legacy",  "state": "stopped", "enabled": false, "configRev": 9, "dependsOn": ["metrics"] }
  ]
}
'@

    $snapshot = Write-Fixture 'snapshot.json' @'
{
  "host": "app-01",
  "collectedAt": "2026-07-15T22:11:00Z",
  "services": [
    { "name": "db",      "state": "running", "enabled": true,  "configRev": 1 },
    { "name": "cache",   "state": "stopped", "enabled": false, "configRev": 2 },
    { "name": "api",     "state": "running", "enabled": true,  "configRev": 5 },
    { "name": "ui",      "state": "stopped", "enabled": true,  "configRev": 2 },
    { "name": "worker",  "state": "running", "enabled": true,  "configRev": 4 },
    { "name": "metrics", "state": "running", "enabled": true,  "configRev": 1 },
    { "name": "legacy",  "state": "running", "enabled": true,  "configRev": 9 },
    { "name": "extra",   "state": "running", "enabled": true,  "configRev": 0 }
  ]
}
'@

    # --- the drifted host: every action class, ordering laws visible ---
    Invoke-Tool @('-Desired', $desired, '-Snapshot', $snapshot)
    Assert-True 'main: exit 0' ($RC -eq 0)
    Assert-Eq 'main: stderr empty' '' $ERR
    Assert-Eq 'main: plan' $mainPlan $OUT

    # --- a reconciled host produces an empty plan ---
    $snapClean = Write-Fixture 'snap_clean.json' @'
{
  "host": "app-02",
  "collectedAt": "2026-07-15T22:12:00Z",
  "services": [
    { "name": "db",      "state": "running", "enabled": true,  "configRev": 3 },
    { "name": "cache",   "state": "running", "enabled": true,  "configRev": 2 },
    { "name": "api",     "state": "running", "enabled": true,  "configRev": 7 },
    { "name": "ui",      "state": "running", "enabled": true,  "configRev": 2 },
    { "name": "worker",  "state": "running", "enabled": true,  "configRev": 4 },
    { "name": "metrics", "state": "stopped", "enabled": false, "configRev": 1 },
    { "name": "legacy",  "state": "stopped", "enabled": false, "configRev": 9 }
  ]
}
'@
    Invoke-Tool @('-Desired', $desired, '-Snapshot', $snapClean)
    Assert-True 'clean: exit 0' ($RC -eq 0)
    Assert-Eq 'clean: no output' '' $OUT
    Assert-Eq 'clean: stderr empty' '' $ERR

    # --- a managed service the collector never saw is a hard error ---
    $snapShort = Write-Fixture 'snap_short.json' @'
{
  "host": "app-03",
  "collectedAt": "2026-07-15T22:13:00Z",
  "services": [
    { "name": "db",      "state": "running", "enabled": true,  "configRev": 3 },
    { "name": "cache",   "state": "running", "enabled": true,  "configRev": 2 },
    { "name": "api",     "state": "running", "enabled": true,  "configRev": 7 },
    { "name": "worker",  "state": "running", "enabled": true,  "configRev": 4 },
    { "name": "metrics", "state": "stopped", "enabled": false, "configRev": 1 },
    { "name": "legacy",  "state": "stopped", "enabled": false, "configRev": 9 }
  ]
}
'@
    Invoke-Tool @('-Desired', $desired, '-Snapshot', $snapShort)
    Assert-True 'short: exit 65' ($RC -eq 65)
    Assert-Eq 'short: stdout empty' '' $OUT
    Assert-Eq 'short: message' "svcplan: service not in snapshot: ui`n" $ERR

    # --- a dependsOn naming no desired service is a hard error ---
    $desBadDep = Write-Fixture 'des_baddep.json' @'
{
  "services": [
    { "name": "a", "state": "running", "enabled": true, "configRev": 1, "dependsOn": [] },
    { "name": "b", "state": "running", "enabled": true, "configRev": 1, "dependsOn": ["ghost"] }
  ]
}
'@
    $snapAb = Write-Fixture 'snap_ab.json' @'
{
  "host": "app-04",
  "collectedAt": "2026-07-15T22:14:00Z",
  "services": [
    { "name": "a", "state": "running", "enabled": true, "configRev": 1 },
    { "name": "b", "state": "running", "enabled": true, "configRev": 1 }
  ]
}
'@
    Invoke-Tool @('-Desired', $desBadDep, '-Snapshot', $snapAb)
    Assert-True 'baddep: exit 65' ($RC -eq 65)
    Assert-Eq 'baddep: stdout empty' '' $OUT
    Assert-Eq 'baddep: message' "svcplan: unknown dependency 'ghost' of 'b'`n" $ERR

    # --- dependency cycles cannot be ordered ---
    $desCycle = Write-Fixture 'des_cycle.json' @'
{
  "services": [
    { "name": "a", "state": "running", "enabled": true, "configRev": 1, "dependsOn": ["b"] },
    { "name": "b", "state": "running", "enabled": true, "configRev": 1, "dependsOn": ["a"] }
  ]
}
'@
    Invoke-Tool @('-Desired', $desCycle, '-Snapshot', $snapAb)
    Assert-True 'cycle: exit 65' ($RC -eq 65)
    Assert-Eq 'cycle: stdout empty' '' $OUT
    Assert-Eq 'cycle: message' "svcplan: dependency cycle detected`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
