# Acceptance harness for aliassync.ps1 and its dry-run support.
# Run from the workspace root:  pwsh -NoProfile -File test_aliassync.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$tool = Join-Path $PSScriptRoot 'aliassync.ps1'
if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
    Write-Output 'FAIL aliassync.ps1 not found in the workspace root'
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

function Write-Fixture {
    param([string]$Name, [string]$Content)
    $p = Join-Path $T $Name
    [System.IO.File]::WriteAllText($p, $Content)
    return $p
}

$aliasDir = ''
$desired = ''
function Reset-Fixture {
    $script:aliasDir = Join-Path $T 'aliases'
    if (Test-Path -LiteralPath $script:aliasDir) {
        Remove-Item -LiteralPath $script:aliasDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $script:aliasDir > $null
    [System.IO.File]::WriteAllText((Join-Path $script:aliasDir 'cache.alias'), "10.0.4.9`n")
    [System.IO.File]::WriteAllText((Join-Path $script:aliasDir 'auth.alias'), "10.0.4.12`n")
    [System.IO.File]::WriteAllText((Join-Path $script:aliasDir 'old-db.alias'), "10.0.2.2`n")
    [System.IO.File]::WriteAllText((Join-Path $script:aliasDir 'notes.txt'), "keep me`n")
    $script:desired = Write-Fixture 'desired.json' @'
{
  "aliases": [
    { "name": "cache", "target": "10.0.4.7" },
    { "name": "edge", "target": "10.0.4.30" },
    { "name": "auth", "target": "10.0.4.12" },
    { "name": "jump", "target": "10.0.9.1" }
  ]
}
'@
}

function Get-DirSnapshot {
    param([string]$Dir)
    $names = @()
    foreach ($f in @(Get-ChildItem -LiteralPath $Dir -File)) { $names += $f.Name }
    [Array]::Sort($names, [System.StringComparer]::Ordinal)
    $parts = @()
    foreach ($n in $names) {
        $raw = [System.IO.File]::ReadAllText((Join-Path $Dir $n))
        $parts += ($n + '=' + ($raw -replace "`n", '/'))
    }
    return ($parts -join '|')
}

$plan = @'
create edge
create jump
update cache
remove old-db

'@

$snapBefore = 'auth.alias=10.0.4.12/|cache.alias=10.0.4.9/|notes.txt=keep me/|old-db.alias=10.0.2.2/'
$snapAfter = 'auth.alias=10.0.4.12/|cache.alias=10.0.4.7/|edge.alias=10.0.4.30/|jump.alias=10.0.9.1/|notes.txt=keep me/'

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- existing behavior: plain run plans and applies ---
    Reset-Fixture
    Assert-Eq 'setup: snapshot' $snapBefore (Get-DirSnapshot $aliasDir)
    Invoke-Tool @('-Desired', $desired, '-Dir', $aliasDir)
    Assert-True 'apply: exit 0' ($RC -eq 0)
    Assert-Eq 'apply: stderr empty' '' $ERR
    Assert-Eq 'apply: plan' $plan $OUT
    Assert-Eq 'apply: directory reconciled' $snapAfter (Get-DirSnapshot $aliasDir)

    # --- existing behavior: a reconciled directory is a no-op ---
    Invoke-Tool @('-Desired', $desired, '-Dir', $aliasDir)
    Assert-True 'noop: exit 0' ($RC -eq 0)
    Assert-Eq 'noop: no output' '' $OUT
    Assert-Eq 'noop: directory unchanged' $snapAfter (Get-DirSnapshot $aliasDir)

    # --- existing behavior: error contract ---
    Reset-Fixture
    $missing = Join-Path $T 'nope.json'
    Invoke-Tool @('-Desired', $missing, '-Dir', $aliasDir)
    Assert-True 'missing desired: exit 66' ($RC -eq 66)
    Assert-Eq 'missing desired: message' "aliassync: desired file not found: $missing`n" $ERR

    $noDir = Join-Path $T 'nodir'
    Invoke-Tool @('-Desired', $desired, '-Dir', $noDir)
    Assert-True 'missing dir: exit 66' ($RC -eq 66)
    Assert-Eq 'missing dir: message' "aliassync: alias dir not found: $noDir`n" $ERR

    $dup = Write-Fixture 'dup.json' @'
{
  "aliases": [
    { "name": "cache", "target": "10.0.4.7" },
    { "name": "cache", "target": "10.0.4.8" }
  ]
}
'@
    Invoke-Tool @('-Desired', $dup, '-Dir', $aliasDir)
    Assert-True 'dup: exit 65' ($RC -eq 65)
    Assert-Eq 'dup: stdout empty' '' $OUT
    Assert-Eq 'dup: message' "aliassync: duplicate alias: cache`n" $ERR
    Assert-Eq 'dup: directory untouched' $snapBefore (Get-DirSnapshot $aliasDir)

    $badName = Write-Fixture 'badname.json' @'
{
  "aliases": [
    { "name": "Web cache", "target": "10.0.4.7" }
  ]
}
'@
    Invoke-Tool @('-Desired', $badName, '-Dir', $aliasDir)
    Assert-True 'badname: exit 65' ($RC -eq 65)
    Assert-Eq 'badname: message' "aliassync: bad alias name: Web cache`n" $ERR

    # --- the feature: -WhatIf dry-runs the exact plan, touching nothing ---
    Reset-Fixture
    Invoke-Tool @('-Desired', $desired, '-Dir', $aliasDir, '-WhatIf')
    Assert-True 'whatif: exit 0' ($RC -eq 0)
    Assert-Eq 'whatif: stderr empty' '' $ERR
    $lines = @($OUT -split "`n" | Where-Object { $_ -cne '' })
    $planLines = @($lines | Where-Object { -not $_.StartsWith('What if: ') })
    $whatifLines = @($lines | Where-Object { $_.StartsWith('What if: ') })
    $planText = ''
    if ($planLines.Count -gt 0) { $planText = (($planLines -join "`n") + "`n") }
    Assert-Eq 'whatif: plan lines' $plan $planText
    Assert-True 'whatif: one gate per mutation' ($whatifLines.Count -eq 4)
    $chatter = $whatifLines -join "`n"
    Assert-True 'whatif: create gate names edge.alias' $chatter.Contains('edge.alias')
    Assert-True 'whatif: create gate names jump.alias' $chatter.Contains('jump.alias')
    Assert-True 'whatif: update gate names cache.alias' $chatter.Contains('cache.alias')
    Assert-True 'whatif: remove gate names old-db.alias' $chatter.Contains('old-db.alias')
    Assert-Eq 'whatif: directory untouched' $snapBefore (Get-DirSnapshot $aliasDir)

    # --- the feature: -Confirm:$false applies without prompting ---
    Reset-Fixture
    Invoke-Tool @('-Desired', $desired, '-Dir', $aliasDir, '-Confirm:$false')
    Assert-True 'confirm-false: exit 0' ($RC -eq 0)
    Assert-Eq 'confirm-false: stderr empty' '' $ERR
    Assert-Eq 'confirm-false: plan' $plan $OUT
    Assert-Eq 'confirm-false: directory reconciled' $snapAfter (Get-DirSnapshot $aliasDir)
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
