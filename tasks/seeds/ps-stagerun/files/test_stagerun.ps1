# Acceptance harness for stagerun.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_stagerun.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'stagerun.ps1') -PathType Leaf)) {
    Write-Output 'FAIL stagerun.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'stagerun.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function Get-WorkText {
    param([string]$Name)
    $p = Join-Path (Join-Path $T 'work') $Name
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { return '(absent)' }
    return [System.IO.File]::ReadAllText($p)
}

function New-WorkArea {
    $work = Join-Path $T 'work'
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path (Join-Path $work 'datadir') > $null
    [System.IO.File]::WriteAllText((Join-Path $work 'notes.txt'), "cache warm notes`n")
    [System.IO.File]::WriteAllText((Join-Path $work 'counts.txt'), "12`n7`n40`n")
    [System.IO.File]::WriteAllText((Join-Path $work 'badcounts.txt'), "12`nseven`n40`n")
    foreach ($n in @('a.dat', 'b.dat', 'c.dat')) {
        [System.IO.File]::WriteAllText((Join-Path (Join-Path $work 'datadir') $n), '')
    }
    [System.IO.File]::WriteAllText((Join-Path $work 'feed.txt'), "line one`nline two`n")
    return $work
}

$planOk = @'
[
  {"name": "warmup", "action": "read", "arg": "notes.txt"},
  {"name": "tally", "action": "sum", "arg": "counts.txt"},
  {"name": "census", "action": "scan", "arg": "datadir"},
  {"name": "feed", "action": "pull", "arg": "feed.txt"}
]
'@

$planMixed = @'
[
  {"name": "warmup", "action": "read", "arg": "gone.txt"},
  {"name": "tally", "action": "sum", "arg": "badcounts.txt"},
  {"name": "census", "action": "scan", "arg": "nodir"},
  {"name": "feed", "action": "pull", "arg": "gone-feed.txt"},
  {"name": "mystery", "action": "compact", "arg": "datadir"},
  {"name": "closing", "action": "read", "arg": "notes.txt"}
]
'@

$okExpected = @'
stage run: plan_ok.json
ok warmup chars=17
ok tally sum=59
ok census files=3
ok feed lines=2
steps: 4 ok: 4 fail: 0

'@

$mixedExpected = @'
stage run: plan_mixed.json
fail warmup FileNotFoundException
fail tally FormatException
fail census DirectoryNotFoundException
fail feed ItemNotFoundException
fail mystery NotSupportedException
ok closing chars=17
failures:
warmup       FileNotFoundException        missing-file
tally        FormatException              bad-number
census       DirectoryNotFoundException   missing-dir
feed         ItemNotFoundException        missing-item
mystery      NotSupportedException        unexpected
steps: 6 ok: 1 fail: 5

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- a clean plan: every step reports, lock released, status written ---
    $work = New-WorkArea
    $planOkPath = Join-Path $T 'plan_ok.json'
    [System.IO.File]::WriteAllText($planOkPath, $planOk + "`n")
    Invoke-Tool @('-Plan', $planOkPath, '-Work', $work)
    Assert-True 'ok plan: exit 0' ($RC -eq 0)
    Assert-Eq 'ok plan: stderr empty' '' $ERR
    Assert-Eq 'ok plan: report' $okExpected $OUT
    Assert-True 'ok plan: lock released' (-not (Test-Path -LiteralPath (Join-Path $work 'stage.lock')))
    Assert-Eq 'ok plan: status written' "steps=4 ok=4 fail=0`n" (Get-WorkText 'last.status')

    # --- a plan where five steps fail five different ways: the run keeps
    # --- going, failures are classified in the table, stderr stays clean,
    # --- and cleanup STILL runs (no lock left, status written) ---
    $work = New-WorkArea
    $planMixedPath = Join-Path $T 'plan_mixed.json'
    [System.IO.File]::WriteAllText($planMixedPath, $planMixed + "`n")
    Invoke-Tool @('-Plan', $planMixedPath, '-Work', $work)
    Assert-True 'mixed plan: exit 70' ($RC -eq 70)
    Assert-Eq 'mixed plan: stderr empty (handled failures do not spew)' '' $ERR
    Assert-Eq 'mixed plan: report' $mixedExpected $OUT
    Assert-True 'mixed plan: lock released despite failures' (-not (Test-Path -LiteralPath (Join-Path $work 'stage.lock')))
    Assert-Eq 'mixed plan: status written despite failures' "steps=6 ok=1 fail=5`n" (Get-WorkText 'last.status')

    # --- a lock somebody else holds is respected: nothing runs, the lock and
    # --- the previous status survive untouched ---
    $work = New-WorkArea
    $lockPath = Join-Path $work 'stage.lock'
    [System.IO.File]::WriteAllText($lockPath, "someone else`n")
    [System.IO.File]::WriteAllText((Join-Path $work 'last.status'), "steps=9 ok=9 fail=0`n")
    Invoke-Tool @('-Plan', $planOkPath, '-Work', $work)
    Assert-True 'held lock: exit 75' ($RC -eq 75)
    Assert-Eq 'held lock: stdout empty' '' $OUT
    Assert-Eq 'held lock: message' "stagerun: lock present: $lockPath`n" $ERR
    Assert-Eq 'held lock: foreign lock untouched' "someone else`n" (Get-WorkText 'stage.lock')
    Assert-Eq 'held lock: previous status untouched' "steps=9 ok=9 fail=0`n" (Get-WorkText 'last.status')

    # --- missing plan file ---
    $work = New-WorkArea
    $gone = Join-Path $T 'gone.json'
    Invoke-Tool @('-Plan', $gone, '-Work', $work)
    Assert-True 'missing plan: exit 66' ($RC -eq 66)
    Assert-Eq 'missing plan: stdout empty' '' $OUT
    Assert-Eq 'missing plan: message' "stagerun: plan not found: $gone`n" $ERR
    Assert-True 'missing plan: no lock created' (-not (Test-Path -LiteralPath (Join-Path $work 'stage.lock')))
    Assert-Eq 'missing plan: no status written' '(absent)' (Get-WorkText 'last.status')

    # --- unparseable plan file ---
    $work = New-WorkArea
    $broken = Join-Path $T 'broken.json'
    [System.IO.File]::WriteAllText($broken, "{`"not`": `"an array`"`n")
    Invoke-Tool @('-Plan', $broken, '-Work', $work)
    Assert-True 'broken plan: exit 65' ($RC -eq 65)
    Assert-Eq 'broken plan: stdout empty' '' $OUT
    Assert-Eq 'broken plan: message' "stagerun: plan is not valid JSON: $broken`n" $ERR
    Assert-True 'broken plan: no lock created' (-not (Test-Path -LiteralPath (Join-Path $work 'stage.lock')))
    Assert-Eq 'broken plan: no status written' '(absent)' (Get-WorkText 'last.status')
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
