# Acceptance harness for flagcard.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_flagcard.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'flagcard.ps1') -PathType Leaf)) {
    Write-Output 'FAIL flagcard.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'flagcard.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function Expect-Card {
    # Expect-Card <ordered pairs: value,int,source per flag> <bound> <explicit>
    param([string[]]$Rows, [string]$Bound, [string]$Explicit)
    $names = @('Archive', 'DryRun', 'FollowSymlinks', 'Purge', 'Rescan')
    $lines = for ($i = 0; $i -lt 5; $i++) {
        "flag $($names[$i]) $($Rows[$i])"
    }
    $lines += "bound: $Bound"
    $lines += "explicit: $Explicit of 5"
    return (($lines -join "`n") + "`n")
}

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- nothing passed: everything defaulted ---
    Invoke-Tool @()
    Assert-True 'bare: exit 0' ($RC -eq 0)
    Assert-Eq 'bare: card' (Expect-Card @(
        'value=False int=0 source=default',
        'value=False int=0 source=default',
        'value=False int=0 source=default',
        'value=False int=0 source=default',
        'value=False int=0 source=default') '(none)' '0') $OUT
    Assert-Eq 'bare: stderr empty' '' $ERR

    # --- plain presence ---
    Invoke-Tool @('-DryRun', '-Purge')
    Assert-True 'two flags: exit 0' ($RC -eq 0)
    Assert-Eq 'two flags: card' (Expect-Card @(
        'value=False int=0 source=default',
        'value=True int=1 source=explicit',
        'value=False int=0 source=default',
        'value=True int=1 source=explicit',
        'value=False int=0 source=default') 'DryRun,Purge' '2') $OUT

    # --- explicit negation: same value as the default, different source ---
    Invoke-Tool @('-Archive:$false')
    Assert-True 'negation: exit 0' ($RC -eq 0)
    Assert-Eq 'negation: card' (Expect-Card @(
        'value=False int=0 source=explicit',
        'value=False int=0 source=default',
        'value=False int=0 source=default',
        'value=False int=0 source=default',
        'value=False int=0 source=default') 'Archive' '1') $OUT

    # --- explicit affirmation via the colon form ---
    Invoke-Tool @('-Rescan:$true')
    Assert-Eq 'colon true: card' (Expect-Card @(
        'value=False int=0 source=default',
        'value=False int=0 source=default',
        'value=False int=0 source=default',
        'value=False int=0 source=default',
        'value=True int=1 source=explicit') 'Rescan' '1') $OUT

    # --- the full mixed shape our runner actually emits ---
    Invoke-Tool @('-Purge', '-DryRun:$false', '-Archive', '-Rescan:$false', '-FollowSymlinks')
    Assert-True 'mixed: exit 0' ($RC -eq 0)
    Assert-Eq 'mixed: card' (Expect-Card @(
        'value=True int=1 source=explicit',
        'value=False int=0 source=explicit',
        'value=True int=1 source=explicit',
        'value=True int=1 source=explicit',
        'value=False int=0 source=explicit') 'Archive,DryRun,FollowSymlinks,Purge,Rescan' '5') $OUT

    # --- argument order never changes the card ---
    Invoke-Tool @('-Purge', '-DryRun')
    $first = $OUT
    Invoke-Tool @('-DryRun', '-Purge')
    Assert-Eq 'order independence' $first $OUT

    # --- a stray positional argument is refused at binding time ---
    Invoke-Tool @('-DryRun', 'now')
    Assert-True 'stray: exit 1' ($RC -eq 1)
    Assert-Eq 'stray: stdout empty' '' $OUT
    Assert-Contains 'stray: engine error' 'positional parameter cannot be found' $ERR

    # --- an unknown flag is refused at binding time ---
    Invoke-Tool @('-Wipe')
    Assert-True 'unknown flag: exit 1' ($RC -eq 1)
    Assert-Eq 'unknown flag: stdout empty' '' $OUT
    Assert-Contains 'unknown flag: engine error' "parameter name 'Wipe'" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
