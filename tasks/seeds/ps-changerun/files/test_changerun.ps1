# Acceptance harness for changerun.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_changerun.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$tool = Join-Path $PSScriptRoot 'changerun.ps1'
if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
    Write-Output 'FAIL changerun.ps1 not found in the workspace root'
    exit 1
}

$T = Join-Path $PSScriptRoot '_t'
$FX = Join-Path $PSScriptRoot 'fixtures'
$desired = Join-Path $FX 'desired.json'
$facts = Join-Path $FX 'facts'
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
    & pwsh -NoProfile -NonInteractive -File $tool @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function Get-Status {
    param([string]$State)
    Invoke-Tool @('status', '-State', $State)
    return $script:OUT
}

$planExpected = @'
planned db01/wal journal: del -> wal
planned db01/pool64 pool: 16 -> 64
planned web01/tls13 tls: 1.2 -> 1.3
planned web01/gzipon gzip: off -> on
planned web01/vhost2 vhost: v1 -> v2
planned web01/banner banner: (none) -> fleet
planned web02/vhost2 vhost: v1 -> v2
'@ + "`n"

$applyCleanExpected = @'
applied db01/wal
applied db01/pool64
applied web01/tls13
applied web01/gzipon
applied web01/vhost2
applied web01/banner
applied web02/vhost2
'@ + "`n"

$applyMixedExpected = @'
applied db01/wal
failed db01/pool64
applied web01/tls13
applied web01/gzipon
failed web01/vhost2
skipped web01/banner
applied web02/vhost2
'@ + "`n"

$rollbackExpected = @'
rolledback db01/wal
rolledback web01/gzipon
rolledback web01/tls13
'@ + "`n"

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- CLI contract ---
    Invoke-Tool @()
    Assert-True 'usage: exit 64' ($RC -eq 64)
    Assert-Eq 'usage: stdout empty' '' $OUT
    Assert-Eq 'usage: message' "usage: changerun.ps1 plan|apply|rollback|status`n" $ERR

    Invoke-Tool @('plan', '-Desired', 'missing_desired.json', '-Facts', $facts, '-State', (Join-Path $T 's0.json'))
    Assert-True 'not found: exit 66' ($RC -eq 66)
    Assert-Eq 'not found: message' "changerun: not found: missing_desired.json`n" $ERR

    # --- plan: dependency order inside each host, hosts ordinal ---
    $s1 = Join-Path $T 's1.json'
    Invoke-Tool @('plan', '-Desired', $desired, '-Facts', $facts, '-State', $s1)
    Assert-True 'plan: exit 0' ($RC -eq 0)
    Assert-Eq 'plan: stderr empty' '' $ERR
    Assert-Eq 'plan: step lines' $planExpected $OUT
    Assert-Eq 'plan: status all planned' "planned=7 applied=0 failed=0 rolled-back=0`n" (Get-Status $s1)

    # --- desired-model validation happens before any state is written ---
    $sBad = Join-Path $T 'sbad.json'
    Invoke-Tool @('plan', '-Desired', (Join-Path $FX 'desired_cycle.json'), '-Facts', $facts, '-State', $sBad)
    Assert-True 'cycle: exit 65' ($RC -eq 65)
    Assert-Eq 'cycle: stdout empty' '' $OUT
    Assert-Eq 'cycle: message' "changerun: requirement cycle detected`n" $ERR
    Assert-True 'cycle: no state file written' (-not (Test-Path -LiteralPath $sBad))

    Invoke-Tool @('plan', '-Desired', (Join-Path $FX 'desired_badreq.json'), '-Facts', $facts, '-State', $sBad)
    Assert-True 'badreq: exit 65' ($RC -eq 65)
    Assert-Eq 'badreq: message' "changerun: unknown requirement 'ghost' of 'vhost2'`n" $ERR
    Assert-True 'badreq: no state file written' (-not (Test-Path -LiteralPath $sBad))

    # --- apply, all executor outcomes ok ---
    Invoke-Tool @('apply', '-State', $s1, '-Results', (Join-Path $FX 'results_clean.json'))
    Assert-True 'apply clean: exit 0' ($RC -eq 0)
    Assert-Eq 'apply clean: stderr empty' '' $ERR
    Assert-Eq 'apply clean: step lines' $applyCleanExpected $OUT
    Assert-Eq 'apply clean: status' "planned=0 applied=7 failed=0 rolled-back=0`n" (Get-Status $s1)

    # --- a state can only be applied once: applied -> applied is illegal ---
    $beforeBytes = [System.IO.File]::ReadAllText($s1)
    Invoke-Tool @('apply', '-State', $s1, '-Results', (Join-Path $FX 'results_clean.json'))
    Assert-True 'double apply: exit 65' ($RC -eq 65)
    Assert-Eq 'double apply: stdout empty' '' $OUT
    Assert-Eq 'double apply: message' "changerun: invalid transition 'applied' -> 'applied' for step 'db01/wal'`n" $ERR
    Assert-Eq 'double apply: state untouched' $beforeBytes ([System.IO.File]::ReadAllText($s1))

    # --- apply with failures: fail stops that host, other hosts continue ---
    $s2 = Join-Path $T 's2.json'
    Invoke-Tool @('plan', '-Desired', $desired, '-Facts', $facts, '-State', $s2)
    Assert-True 'plan for mixed: exit 0' ($RC -eq 0)
    Invoke-Tool @('apply', '-State', $s2, '-Results', (Join-Path $FX 'results_mixed.json'))
    Assert-True 'apply mixed: exit 65 when any step failed' ($RC -eq 65)
    Assert-Eq 'apply mixed: step lines' $applyMixedExpected $OUT
    Assert-Eq 'apply mixed: status' "planned=1 applied=4 failed=2 rolled-back=0`n" (Get-Status $s2)

    # --- rollback: only hosts with a failure, applied steps, reverse order ---
    Invoke-Tool @('rollback', '-State', $s2)
    Assert-True 'rollback: exit 0' ($RC -eq 0)
    Assert-Eq 'rollback: stderr empty' '' $ERR
    Assert-Eq 'rollback: step lines' $rollbackExpected $OUT
    Assert-Eq 'rollback: status' "planned=1 applied=1 failed=2 rolled-back=3`n" (Get-Status $s2)

    # --- rollback is idempotent ---
    Invoke-Tool @('rollback', '-State', $s2)
    Assert-True 'rollback twice: exit 0' ($RC -eq 0)
    Assert-Eq 'rollback twice: nothing to do' '' $OUT
    Assert-Eq 'rollback twice: status unchanged' "planned=1 applied=1 failed=2 rolled-back=3`n" (Get-Status $s2)

    # --- rolled-back steps can never be re-applied in place ---
    Invoke-Tool @('apply', '-State', $s2, '-Results', (Join-Path $FX 'results_clean.json'))
    Assert-True 'apply after rollback: exit 65' ($RC -eq 65)
    Assert-Eq 'apply after rollback: message' "changerun: invalid transition 'rolled-back' -> 'applied' for step 'db01/wal'`n" $ERR

    # --- unknown status in a state file is rejected by every command ---
    $sHeld = Join-Path $T 'sheld.json'
    Copy-Item -LiteralPath (Join-Path $FX 'state_unknown.json') -Destination $sHeld
    Invoke-Tool @('status', '-State', $sHeld)
    Assert-True 'unknown status via status: exit 65' ($RC -eq 65)
    Assert-Eq 'unknown status via status: message' "changerun: unknown status 'held' for step 'web01/tls13'`n" $ERR
    Invoke-Tool @('apply', '-State', $sHeld, '-Results', (Join-Path $FX 'results_clean.json'))
    Assert-True 'unknown status via apply: exit 65' ($RC -eq 65)
    Assert-Eq 'unknown status via apply: message' "changerun: unknown status 'held' for step 'web01/tls13'`n" $ERR

    # --- missing executor outcome: validated before anything transitions ---
    $s3 = Join-Path $T 's3.json'
    Invoke-Tool @('plan', '-Desired', $desired, '-Facts', $facts, '-State', $s3)
    Assert-True 'plan for missing-outcome: exit 0' ($RC -eq 0)
    Invoke-Tool @('apply', '-State', $s3, '-Results', (Join-Path $FX 'results_missing.json'))
    Assert-True 'missing outcome: exit 65' ($RC -eq 65)
    Assert-Eq 'missing outcome: stdout empty' '' $OUT
    Assert-Eq 'missing outcome: message' "changerun: no outcome for step 'web01/gzipon'`n" $ERR
    Assert-Eq 'missing outcome: nothing transitioned' "planned=7 applied=0 failed=0 rolled-back=0`n" (Get-Status $s3)

    # --- a fully converged fleet plans to an empty, appliable state ---
    $factsSolo = Join-Path $T 'facts_solo'
    New-Item -ItemType Directory -Force -Path $factsSolo > $null
    Copy-Item -LiteralPath (Join-Path $facts 'web03.json') -Destination $factsSolo
    $s4 = Join-Path $T 's4.json'
    Invoke-Tool @('plan', '-Desired', $desired, '-Facts', $factsSolo, '-State', $s4)
    Assert-True 'empty plan: exit 0' ($RC -eq 0)
    Assert-Eq 'empty plan: no output' '' $OUT
    Assert-Eq 'empty plan: status' "planned=0 applied=0 failed=0 rolled-back=0`n" (Get-Status $s4)
    Invoke-Tool @('apply', '-State', $s4, '-Results', (Join-Path $FX 'results_clean.json'))
    Assert-True 'empty apply: exit 0' ($RC -eq 0)
    Assert-Eq 'empty apply: no output' '' $OUT
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
