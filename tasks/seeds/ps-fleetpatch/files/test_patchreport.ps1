# Regression harness for patchreport.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_patchreport.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'patchreport.ps1') -PathType Leaf)) {
    Write-Output 'FAIL patchreport.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'patchreport.ps1') @CaseArgs 1>$outFile 2>$errFile
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

$mainSnapshot = @'
[
  {"name": "ams-kiosk-1", "site": "ams", "owners": ["retail-ops"], "status": "ok"},
  {"name": "ams-kiosk-2", "site": "ams", "owners": [], "status": "ok"},
  {"name": "fra-kiosk-1", "site": "fra", "owners": ["retail-ops", "field"], "status": "overdue"},
  {"name": "fra-kiosk-2", "site": "fra", "owners": ["field"], "status": "overdue"},
  {"name": "fra-kiosk-3", "site": "fra", "owners": ["retail-ops"], "status": "overdue"},
  {"name": "mgmt-probe-9", "site": "fra", "owners": null, "status": "ok"},
  {"name": "zrh-kiosk-1", "site": "zrh", "owners": ["retail-ops"], "status": "ok"},
  {"name": "zrh-kiosk-4", "site": "zrh", "owners": [""], "status": "ok"}
]
'@

$mainExpected = @'
patch status report
== site ams ==
  ams-kiosk-1     retail-ops
  ams-kiosk-2     (unassigned)
== site fra ==
! fra-kiosk-1     retail-ops, field
! fra-kiosk-2     field
! fra-kiosk-3     retail-ops
== site zrh ==
  zrh-kiosk-1     retail-ops
  zrh-kiosk-4     (unassigned)
hosts listed: 7
overdue sites: 1

'@

$twoExpected = @'
patch status report
== site ams ==
! ams-kiosk-1     retail-ops
== site zrh ==
! zrh-kiosk-1     field
  zrh-kiosk-2     field
hosts listed: 3
overdue sites: 2

'@

$calmExpected = @'
patch status report
== site ams ==
  ams-kiosk-1     retail-ops
  ams-kiosk-2     (unassigned)
hosts listed: 2
overdue sites: 0

'@

$loneExpected = @'
patch status report
== site lis ==
! lis-kiosk-1     (unassigned)
hosts listed: 1
overdue sites: 1

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- the shift-report snapshot: every host the collector attributed must
    # --- appear, blank owner lists render as (unassigned), null-owner probe
    # --- records are skipped, and the overdue-site count is a SITE count ---
    $main = Write-Fixture 'hosts.json' $mainSnapshot
    Invoke-Tool @('-Path', $main)
    Assert-True 'main: exit 0' ($RC -eq 0)
    Assert-Eq 'main: stderr empty' '' $ERR
    Assert-Eq 'main: report' $mainExpected $OUT

    # --- overdue hosts across two sites count as two sites ---
    $two = Write-Fixture 'two.json' '[{"name": "ams-kiosk-1", "site": "ams", "owners": ["retail-ops"], "status": "overdue"}, {"name": "zrh-kiosk-1", "site": "zrh", "owners": ["field"], "status": "overdue"}, {"name": "zrh-kiosk-2", "site": "zrh", "owners": ["field"], "status": "ok"}]'
    Invoke-Tool @('-Path', $two)
    Assert-True 'two: exit 0' ($RC -eq 0)
    Assert-Eq 'two: report' $twoExpected $OUT

    # --- a fully patched fleet has zero overdue sites ---
    $calm = Write-Fixture 'calm.json' '[{"name": "ams-kiosk-1", "site": "ams", "owners": ["retail-ops"], "status": "ok"}, {"name": "ams-kiosk-2", "site": "ams", "owners": [], "status": "ok"}]'
    Invoke-Tool @('-Path', $calm)
    Assert-True 'calm: exit 0' ($RC -eq 0)
    Assert-Eq 'calm: stderr empty' '' $ERR
    Assert-Eq 'calm: report' $calmExpected $OUT

    # --- a one-host snapshot still reports that host ---
    $lone = Write-Fixture 'lone.json' '[{"name": "lis-kiosk-1", "site": "lis", "owners": [""], "status": "overdue"}]'
    Invoke-Tool @('-Path', $lone)
    Assert-True 'lone: exit 0' ($RC -eq 0)
    Assert-Eq 'lone: report' $loneExpected $OUT

    # --- missing snapshot ---
    $gone = Join-Path $T 'gone.json'
    Invoke-Tool @('-Path', $gone)
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "patchreport: snapshot not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
