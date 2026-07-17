# Acceptance harness for probefeed.ps1 and its incremental checkpoint mode.
# Run from the workspace root:  pwsh -NoProfile -File test_probefeed.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$tool = Join-Path $PSScriptRoot 'probefeed.ps1'
if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
    Write-Output 'FAIL probefeed.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File $tool @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function Write-Spool {
    param([string]$Name, [string]$Content)
    [System.IO.File]::WriteAllText((Join-Path (Join-Path $T 'spool') $Name), $Content)
}

function Read-Text {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '(missing)' }
    return [System.IO.File]::ReadAllText($Path)
}

function Read-CheckpointNames {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '(missing)' }
    try {
        $state = ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($Path))
        if (@($state.PSObject.Properties.Name) -cnotcontains 'done') { return '(no done key)' }
        return (@($state.done) -join ',')
    } catch {
        return '(unparseable)'
    }
}

$ledger3 = @'
{"file":"alpha.json","probe":"dns-a","host":"web01","status":"ok","latencyMs":12}
{"file":"gamma.json","probe":"http-root","host":"web02","status":"fail","latencyMs":340}
{"file":"mu.json","probe":"tcp-5432","host":"db01","status":"ok","latencyMs":8}

'@

$deltaLine = @'
{"file":"delta.json","probe":"http-health","host":"web03","status":"fail","latencyMs":77}

'@

$ledger4 = @'
{"file":"alpha.json","probe":"dns-a","host":"web01","status":"ok","latencyMs":12}
{"file":"delta.json","probe":"http-health","host":"web03","status":"fail","latencyMs":77}
{"file":"gamma.json","probe":"http-root","host":"web02","status":"fail","latencyMs":340}
{"file":"mu.json","probe":"tcp-5432","host":"db01","status":"ok","latencyMs":8}

'@

$skippedErr = @'
probefeed: skipped beta.json: malformed
probefeed: skipped zeta.json: malformed

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null
    New-Item -ItemType Directory -Force -Path (Join-Path $T 'spool') > $null
    $spool = Join-Path $T 'spool'

    Write-Spool 'alpha.json' '{ "probe": "dns-a", "host": "web01", "status": "ok", "latencyMs": 12 }'
    Write-Spool 'beta.json' '{ this is not json'
    Write-Spool 'gamma.json' '{ "probe": "http-root", "host": "web02", "status": "fail", "latencyMs": 340 }'
    Write-Spool 'mu.json' '{ "probe": "tcp-5432", "host": "db01", "status": "ok", "latencyMs": 8 }'
    Write-Spool 'zeta.json' '{ "probe": "icmp", "host": "web01", "status": "OK", "latencyMs": 5 }'
    Write-Spool 'notes.txt' 'not a probe drop'

    # ---------------------------------------------------------------
    # Existing behavior, frozen: a plain run folds everything, every
    # time -- which is exactly why reruns double the ledger today.
    # ---------------------------------------------------------------

    $out1 = Join-Path $T 'ledger1.ndjson'
    Invoke-Tool @('-Spool', $spool, '-Out', $out1)
    Assert-True 'plain: exit 0' ($RC -eq 0)
    Assert-Eq 'plain: summary' "processed 3, skipped 2, ok=2, fail=1`n" $OUT
    Assert-Eq 'plain: skip reports' $skippedErr $ERR
    Assert-Eq 'plain: ledger' $ledger3 (Read-Text $out1)

    Invoke-Tool @('-Spool', $spool, '-Out', $out1)
    Assert-True 'plain rerun: exit 0' ($RC -eq 0)
    Assert-Eq 'plain rerun: summary' "processed 3, skipped 2, ok=2, fail=1`n" $OUT
    Assert-Eq 'plain rerun: ledger doubled' ($ledger3.TrimEnd("`n") + "`n" + $ledger3) (Read-Text $out1)

    $noSpool = Join-Path $T 'nospool'
    Invoke-Tool @('-Spool', $noSpool, '-Out', (Join-Path $T 'x.ndjson'))
    Assert-True 'missing spool: exit 66' ($RC -eq 66)
    Assert-Eq 'missing spool: message' "probefeed: spool not found: $noSpool`n" $ERR

    # ---------------------------------------------------------------
    # The feature: -Checkpoint remembers handled drops across runs.
    # ---------------------------------------------------------------

    $cp1 = Join-Path $T 'state1.json'
    $out2 = Join-Path $T 'ledger2.ndjson'
    Invoke-Tool @('-Spool', $spool, '-Out', $out2, '-Checkpoint', $cp1)
    Assert-True 'first: exit 0' ($RC -eq 0)
    Assert-Eq 'first: summary' "processed 3, skipped 2, ok=2, fail=1, seen=0`n" $OUT
    Assert-Eq 'first: skip reports' $skippedErr $ERR
    Assert-Eq 'first: ledger' $ledger3 (Read-Text $out2)
    Assert-Eq 'first: checkpoint records everything handled' 'alpha.json,beta.json,gamma.json,mu.json,zeta.json' (Read-CheckpointNames $cp1)

    $cpBytes = Read-Text $cp1
    Invoke-Tool @('-Spool', $spool, '-Out', $out2, '-Checkpoint', $cp1)
    Assert-True 'second: exit 0' ($RC -eq 0)
    Assert-Eq 'second: zero new work' "processed 0, skipped 0, ok=0, fail=0, seen=5`n" $OUT
    Assert-Eq 'second: stderr quiet' '' $ERR
    Assert-Eq 'second: ledger untouched' $ledger3 (Read-Text $out2)
    Assert-Eq 'second: checkpoint stable' $cpBytes (Read-Text $cp1)

    Write-Spool 'delta.json' '{ "probe": "http-health", "host": "web03", "status": "fail", "latencyMs": 77 }'
    Invoke-Tool @('-Spool', $spool, '-Out', $out2, '-Checkpoint', $cp1)
    Assert-True 'new drop: exit 0' ($RC -eq 0)
    Assert-Eq 'new drop: only the new file' "processed 1, skipped 0, ok=0, fail=1, seen=5`n" $OUT
    Assert-Eq 'new drop: ledger grew by one line' ($ledger3.TrimEnd("`n") + "`n" + $deltaLine) (Read-Text $out2)
    Assert-Eq 'new drop: checkpoint caught up' 'alpha.json,beta.json,delta.json,gamma.json,mu.json,zeta.json' (Read-CheckpointNames $cp1)

    # --- corrupt checkpoint: warn, rebuild, keep going ---
    $cp2 = Join-Path $T 'state2.json'
    [System.IO.File]::WriteAllText($cp2, '{ nope')
    $out3 = Join-Path $T 'ledger3.ndjson'
    Invoke-Tool @('-Spool', $spool, '-Out', $out3, '-Checkpoint', $cp2)
    Assert-True 'corrupt: exit 0' ($RC -eq 0)
    Assert-Eq 'corrupt: summary' "processed 4, skipped 2, ok=2, fail=2, seen=0`n" $OUT
    Assert-Eq 'corrupt: warning then skips' ("probefeed: checkpoint unreadable, rebuilding`n" + $skippedErr) $ERR
    Assert-Eq 'corrupt: full reprocess' $ledger4 (Read-Text $out3)
    Assert-Eq 'corrupt: checkpoint rebuilt' 'alpha.json,beta.json,delta.json,gamma.json,mu.json,zeta.json' (Read-CheckpointNames $cp2)

    $cp3 = Join-Path $T 'state3.json'
    [System.IO.File]::WriteAllText($cp3, '{ "done": 42 }')
    $out4 = Join-Path $T 'ledger4.ndjson'
    Invoke-Tool @('-Spool', $spool, '-Out', $out4, '-Checkpoint', $cp3)
    Assert-True 'wrong shape: exit 0' ($RC -eq 0)
    Assert-Eq 'wrong shape: summary' "processed 4, skipped 2, ok=2, fail=2, seen=0`n" $OUT
    Assert-Eq 'wrong shape: warning then skips' ("probefeed: checkpoint unreadable, rebuilding`n" + $skippedErr) $ERR
    Assert-Eq 'wrong shape: checkpoint rebuilt' 'alpha.json,beta.json,delta.json,gamma.json,mu.json,zeta.json' (Read-CheckpointNames $cp3)
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
