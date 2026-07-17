# Regression harness for svcsnap.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_svcsnap.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'svcsnap.ps1') -PathType Leaf)) {
    Write-Output 'FAIL svcsnap.ps1 not found in the workspace root'
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

function Assert-Contains {
    param([string]$Label, [string]$Needle, [string]$Haystack)
    $script:checks++
    if ($Haystack.Contains($Needle, [System.StringComparison]::Ordinal)) { return }
    $script:fails++
    Write-Output "FAIL $Label"
    Write-Output '--- wanted substring ---'
    Write-Output $Needle
    Write-Output '--- actual ---'
    Write-Output $Haystack
    Write-Output '----------------'
}

$script:RC = 0
$script:OUT = ''
$script:ERR = ''
function Invoke-Tool {
    param([string[]]$CaseArgs = @())
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'svcsnap.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    $snap = Join-Path $T 'day.json'
    [System.IO.File]::WriteAllText($snap, @'
[
  {"name": "relay-2", "state": "running", "restarts": 0},
  {"name": "Cache", "state": "failed", "restarts": 4},
  {"name": "api-gw", "state": "running", "restarts": 1},
  {"name": "zebra-sync", "state": "stopped", "restarts": 2},
  {"name": "auth", "state": "failed", "restarts": 7},
  {"name": "db-main", "state": "running", "restarts": 0},
  {"name": "metrics", "state": "stopped", "restarts": 3}
]
'@)

    $allRows = "Cache failed restarts=4`napi-gw running restarts=1`nauth failed restarts=7`ndb-main running restarts=0`nmetrics stopped restarts=3`nrelay-2 running restarts=0`nzebra-sync stopped restarts=2`n"

    # --- the dashboard cron's first call shape: list the whole snapshot ---
    Invoke-Tool @('-Snapshot', $snap)
    Assert-True 'list all: exit 0' ($RC -eq 0)
    Assert-Eq 'list all: rows ordinal by name' $allRows $OUT
    Assert-Eq 'list all: stderr empty' '' $ERR

    # --- listing with a cap ---
    Invoke-Tool @('-Snapshot', $snap, '-Limit', '3')
    Assert-True 'list capped: exit 0' ($RC -eq 0)
    Assert-Eq 'list capped: first three rows' "Cache failed restarts=4`napi-gw running restarts=1`nauth failed restarts=7`n" $OUT

    # --- the dashboard cron's second call shape: state filter with a cap ---
    Invoke-Tool @('-Snapshot', $snap, '-State', 'failed', '-Limit', '1')
    Assert-True 'state capped: exit 0' ($RC -eq 0)
    Assert-Eq 'state capped: row' "Cache failed restarts=4`n" $OUT

    # --- plain state filter ---
    Invoke-Tool @('-Snapshot', $snap, '-State', 'running')
    Assert-True 'state: exit 0' ($RC -eq 0)
    Assert-Eq 'state: rows' "api-gw running restarts=1`ndb-main running restarts=0`nrelay-2 running restarts=0`n" $OUT

    # --- name lookup, with and without a cap ---
    Invoke-Tool @('-Snapshot', $snap, '-Name', 'db-main')
    Assert-True 'name: exit 0' ($RC -eq 0)
    Assert-Eq 'name: row' "db-main running restarts=0`n" $OUT

    Invoke-Tool @('-Snapshot', $snap, '-Name', 'db-main', '-Limit', '5')
    Assert-True 'name capped: exit 0' ($RC -eq 0)
    Assert-Eq 'name capped: row' "db-main running restarts=0`n" $OUT

    # --- name matching is case-sensitive; a miss is empty, not an error ---
    Invoke-Tool @('-Snapshot', $snap, '-Name', 'cache')
    Assert-True 'name miss: exit 0' ($RC -eq 0)
    Assert-Eq 'name miss: no rows' '' $OUT

    # --- counting ---
    Invoke-Tool @('-Snapshot', $snap, '-CountOnly')
    Assert-True 'count all: exit 0' ($RC -eq 0)
    Assert-Eq 'count all: value' "count=7`n" $OUT

    Invoke-Tool @('-Snapshot', $snap, '-State', 'stopped', '-CountOnly')
    Assert-True 'count stopped: exit 0' ($RC -eq 0)
    Assert-Eq 'count stopped: value' "count=2`n" $OUT

    # --- -Name and -State are exclusive on purpose and must stay that way ---
    Invoke-Tool @('-Snapshot', $snap, '-Name', 'auth', '-State', 'running')
    Assert-True 'exclusive: exit 1' ($RC -eq 1)
    Assert-Eq 'exclusive: stdout empty' '' $OUT
    Assert-Contains 'exclusive: binding refuses the pair' 'Parameter set cannot be resolved' $ERR

    # --- bad state value dies at binding time ---
    Invoke-Tool @('-Snapshot', $snap, '-State', 'crashed')
    Assert-True 'bad state: exit 1' ($RC -eq 1)
    Assert-Contains 'bad state: set named' 'does not belong to the set' $ERR

    # --- missing snapshot file ---
    $gone = Join-Path $T 'gone.json'
    Invoke-Tool @('-Snapshot', $gone)
    Assert-True 'missing snapshot: exit 2' ($RC -eq 2)
    Assert-Eq 'missing snapshot: stdout empty' '' $OUT
    Assert-Eq 'missing snapshot: message' "svcsnap: snapshot not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
