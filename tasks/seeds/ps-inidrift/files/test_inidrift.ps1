# Acceptance harness for inidrift.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_inidrift.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$tool = Join-Path $PSScriptRoot 'inidrift.ps1'
if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
    Write-Output 'FAIL inidrift.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'inidrift.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function Write-Fixture {
    param([string]$Rel, [string]$Content)
    $p = Join-Path $T $Rel
    [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($p)) > $null
    [System.IO.File]::WriteAllText($p, $Content)
    return $p
}

$driftExpected = @'
mismatch cache.ini [main] policy expected=lru actual=arc
missing-file db.ini
missing-key web.ini [limits] burst
mismatch web.ini [limits] maxconn expected=512 actual=480
missing-section web.ini [logging]
mismatch web.ini [server] hostname expected=edge-01 actual=edge-02
mismatch web.ini [server] workers expected=4 actual=many

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # desired.json is deliberately NOT in ordinal file order
    $desired = Write-Fixture 'desired.json' @'
{
  "web.ini": {
    "server": { "port": 8080, "tls": true, "hostname": "edge-01", "workers": 4 },
    "logging": { "level": "info" },
    "limits": { "maxconn": 512, "burst": 64 }
  },
  "db.ini": {
    "pool": { "size": 10 }
  },
  "cache.ini": {
    "main": { "sizeMb": 256, "policy": "lru" }
  }
}
'@

    $snap = Join-Path $T 'snap'
    Write-Fixture 'snap/web.ini' @'
; deployed by fleet-push 44
[server]
port = 08080
tls = True
hostname = edge-02
workers = many

[limits]
maxconn = 512
maxconn = 480
# burst intentionally unset until the capacity review
'@ > $null
    Write-Fixture 'snap/cache.ini' @'
[main]
sizeMb = 256
policy = arc
Policy = lru
'@ > $null
    Write-Fixture 'snap/notes.ini' @'
[scratch]
owner = ops
'@ > $null

    # --- the drifted snapshot ---
    Invoke-Tool @('-Desired', $desired, '-SnapshotRoot', $snap)
    Assert-True 'drift: exit 65' ($RC -eq 65)
    Assert-Eq 'drift: stderr empty' '' $ERR
    Assert-Eq 'drift: report' $driftExpected $OUT

    # --- a clean snapshot: typed compares tolerate 004 and FALSE ---
    $desired2 = Write-Fixture 'desired2.json' @'
{
  "app.ini": {
    "core": { "threads": 4, "debug": false, "name": "relay 9", "banner": "a = b" }
  }
}
'@
    $snap2 = Join-Path $T 'snap2'
    Write-Fixture 'snap2/app.ini' @'
# generated file, do not edit
[core]
threads = 004
debug = FALSE
name = relay 9
banner = a = b
'@ > $null
    Invoke-Tool @('-Desired', $desired2, '-SnapshotRoot', $snap2)
    Assert-True 'clean: exit 0' ($RC -eq 0)
    Assert-Eq 'clean: stdout empty' '' $OUT
    Assert-Eq 'clean: stderr empty' '' $ERR

    # --- missing desired file ---
    $noDesired = Join-Path $T 'nope.json'
    Invoke-Tool @('-Desired', $noDesired, '-SnapshotRoot', $snap)
    Assert-True 'nodesired: exit 66' ($RC -eq 66)
    Assert-Eq 'nodesired: stdout empty' '' $OUT
    Assert-Eq 'nodesired: message' "inidrift: desired file not found: $noDesired`n" $ERR

    # --- missing snapshot root ---
    $noSnap = Join-Path $T 'nosnap'
    Invoke-Tool @('-Desired', $desired, '-SnapshotRoot', $noSnap)
    Assert-True 'nosnap: exit 66' ($RC -eq 66)
    Assert-Eq 'nosnap: stdout empty' '' $OUT
    Assert-Eq 'nosnap: message' "inidrift: snapshot root not found: $noSnap`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
