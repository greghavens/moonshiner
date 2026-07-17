# Acceptance harness for litmatch.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_litmatch.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'litmatch.ps1') -PathType Leaf)) {
    Write-Output 'FAIL litmatch.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'litmatch.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

# One behavior-table case: run against the journal, compare matched lines.
function Assert-Case {
    param([string]$Label, [string]$Pattern, [string]$Mode, [bool]$Cased, [string[]]$ExpectedLines)
    $caseArgs = @('-Path', $script:journal, '-Pattern', $Pattern, '-Mode', $Mode)
    if ($Cased) { $caseArgs += '-CaseSensitive' }
    Invoke-Tool $caseArgs
    Assert-True "${Label}: exit 0" ($RC -eq 0)
    Assert-Eq "${Label}: stderr empty" '' $ERR
    $expected = ''
    foreach ($l in $ExpectedLines) { $expected += $l + "`n" }
    Assert-Eq "${Label}: lines" $expected $OUT
}

$journalText = @'
disk[0] rebuild queued
disk0 rebuild queued
DISK[0] REBUILD QUEUED
probe 10.2.30.4 timeout
probe 10x2y30z4 timeout
job a+b done
job aab done
note ends with $
cache*hot path
cacheXhot path
what? exactly
whatX exactly
'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null
    $script:journal = Join-Path $T 'journal.txt'
    [System.IO.File]::WriteAllText($script:journal, $journalText + "`n")

    # --- literal mode: the pattern is plain text, never syntax ---
    Assert-Case 'lit-brackets' 'disk[0]' 'literal' $false @(
        'disk[0] rebuild queued',
        'DISK[0] REBUILD QUEUED')
    Assert-Case 'lit-brackets-cased' 'disk[0]' 'literal' $true @(
        'disk[0] rebuild queued')
    Assert-Case 'lit-dots' '10.2.30.4' 'literal' $false @(
        'probe 10.2.30.4 timeout')
    Assert-Case 'lit-plus' 'a+b' 'literal' $false @(
        'job a+b done')
    Assert-Case 'lit-star' 'cache*hot' 'literal' $false @(
        'cache*hot path')
    Assert-Case 'lit-nohit' 'zzz' 'literal' $false @()

    # --- regex mode: same texts, now treated as syntax ---
    Assert-Case 'rx-brackets' 'disk[0]' 'regex' $false @(
        'disk0 rebuild queued')
    Assert-Case 'rx-dots' '10.2.30.4' 'regex' $false @(
        'probe 10.2.30.4 timeout',
        'probe 10x2y30z4 timeout')
    Assert-Case 'rx-plus' 'a+b' 'regex' $false @(
        'job aab done')
    Assert-Case 'rx-cased' 'DISK' 'regex' $true @(
        'DISK[0] REBUILD QUEUED')

    # --- wildcard mode: whole-line globs ---
    Assert-Case 'wc-class' 'disk[0]*' 'wildcard' $false @(
        'disk0 rebuild queued')
    Assert-Case 'wc-dollar' '*$' 'wildcard' $false @(
        'note ends with $')
    Assert-Case 'wc-star' 'cache*hot*' 'wildcard' $false @(
        'cache*hot path',
        'cacheXhot path')
    Assert-Case 'wc-any' 'what? *' 'wildcard' $false @(
        'what? exactly',
        'whatX exactly')
    Assert-Case 'wc-escaped' 'what[?] *' 'wildcard' $false @(
        'what? exactly')

    # --- an unusable regex is refused, not passed through ---
    Invoke-Tool @('-Path', $script:journal, '-Pattern', '(', '-Mode', 'regex')
    Assert-True 'badregex: exit 65' ($RC -eq 65)
    Assert-Eq 'badregex: stdout empty' '' $OUT
    Assert-Eq 'badregex: message' "litmatch: invalid regex: (`n" $ERR

    # --- an unknown mode is refused ---
    Invoke-Tool @('-Path', $script:journal, '-Pattern', 'x', '-Mode', 'glob')
    Assert-True 'badmode: nonzero exit' ($RC -ne 0)
    Assert-Eq 'badmode: stdout empty' '' $OUT

    # --- missing journal ---
    $gone = Join-Path $T 'gone.txt'
    Invoke-Tool @('-Path', $gone, '-Pattern', 'x', '-Mode', 'literal')
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "litmatch: file not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
