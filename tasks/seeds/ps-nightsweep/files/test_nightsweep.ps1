# Regression harness for nightsweep.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_nightsweep.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'nightsweep.ps1') -PathType Leaf)) {
    Write-Output 'FAIL nightsweep.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'nightsweep.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

$planJson = @'
[
  {"name": "mon-log", "action": "archive", "src": "logs/app-mon.log", "dest": "archive/app-mon.log"},
  {"name": "tue-log", "action": "archive", "src": "logs/app-tue.log", "dest": "archive/app-tue.log"},
  {"name": "old-1", "action": "purge", "src": "stage/old-1.tmp"},
  {"name": "old-2", "action": "purge", "src": "stage/old-2.tmp"}
]
'@

function New-SweepRoot {
    # lays out the fixture tree; which sources exist is up to the case
    param([string[]]$Files)
    $root = Join-Path $T 'root'
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path (Join-Path $root 'logs') > $null
    New-Item -ItemType Directory -Force -Path (Join-Path $root 'stage') > $null
    foreach ($f in $Files) {
        [System.IO.File]::WriteAllText((Join-Path $root $f), "body of $f`n")
    }
    return $root
}

$mixedExpected = @'
task mon-log: ok
task tue-log: FAILED
task old-1: ok
task old-2: FAILED
swept: 4 tasks, 2 failed

'@

$cleanExpected = @'
task mon-log: ok
task tue-log: ok
task old-1: ok
task old-2: ok
swept: 4 tasks, 0 failed

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null
    $plan = Join-Path $T 'sweep.json'
    [System.IO.File]::WriteAllText($plan, $planJson)

    # --- a run with missing sources: the tasks whose work did not happen are
    # --- reported FAILED, the summary counts them, the run exits 65, later
    # --- tasks still run, and nothing sprays raw error records ---
    $root = New-SweepRoot -Files @('logs/app-mon.log', 'stage/old-1.tmp')
    Invoke-Tool @('-Plan', $plan, '-Root', $root)
    Assert-True 'mixed: exit 65' ($RC -eq 65)
    Assert-Eq 'mixed: stderr empty' '' $ERR
    Assert-Eq 'mixed: report' $mixedExpected $OUT
    Assert-True 'mixed: archived copy landed' (Test-Path -LiteralPath (Join-Path $root 'archive/app-mon.log') -PathType Leaf)
    Assert-Eq 'mixed: archived copy content' "body of logs/app-mon.log`n" ([System.IO.File]::ReadAllText((Join-Path $root 'archive/app-mon.log')))
    Assert-True 'mixed: failed archive left nothing' (-not (Test-Path -LiteralPath (Join-Path $root 'archive/app-tue.log')))
    Assert-True 'mixed: purged file gone' (-not (Test-Path -LiteralPath (Join-Path $root 'stage/old-1.tmp')))

    # --- a clean run: everything ok, exit 0, quiet stderr ---
    $root = New-SweepRoot -Files @('logs/app-mon.log', 'logs/app-tue.log', 'stage/old-1.tmp', 'stage/old-2.tmp')
    Invoke-Tool @('-Plan', $plan, '-Root', $root)
    Assert-True 'clean: exit 0' ($RC -eq 0)
    Assert-Eq 'clean: stderr empty' '' $ERR
    Assert-Eq 'clean: report' $cleanExpected $OUT
    Assert-True 'clean: both archives landed' ((Test-Path -LiteralPath (Join-Path $root 'archive/app-mon.log')) -and (Test-Path -LiteralPath (Join-Path $root 'archive/app-tue.log')))
    Assert-True 'clean: stage emptied' (@(Get-ChildItem -LiteralPath (Join-Path $root 'stage')).Count -eq 0)

    # --- missing plan file ---
    $gone = Join-Path $T 'gone.json'
    Invoke-Tool @('-Plan', $gone, '-Root', $root)
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "nightsweep: plan not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
