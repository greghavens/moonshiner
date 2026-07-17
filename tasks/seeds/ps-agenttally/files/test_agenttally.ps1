# Regression harness for agenttally.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_agenttally.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'agenttally.ps1') -PathType Leaf)) {
    Write-Output 'FAIL agenttally.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'agenttally.ps1') @CaseArgs 1>$outFile 2>$errFile
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

$mainCsv = @'
agent,seq
k7ax,1
K7AX,2
aa19,3
k7ax,4
QB2c,5
k7ax,6
K7AX,7
qb2c,8
'@

$mainExpected = @'
agent check-in tally
K7AX      2
QB2c      1
aa19      1
k7ax      3
qb2c      1
agents: 5

'@

$mutedExpected = @'
agent check-in tally
K7AX      2
QB2c      1
aa19      1
qb2c      1
agents: 4

'@

$plainCsv = @'
agent,seq
relay,1
gate,2
relay,3
'@

$plainExpected = @'
agent check-in tally
gate      1
relay     2
agents: 2

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- ids from the gateway are matched byte-for-byte: each distinct id
    # --- keeps its own row and its own count, in byte order ---
    $main = Write-Fixture 'checkins.csv' $mainCsv
    Invoke-Tool @('-Path', $main)
    Assert-True 'main: exit 0' ($RC -eq 0)
    Assert-Eq 'main: stderr empty' '' $ERR
    Assert-Eq 'main: tally' $mainExpected $OUT

    # --- muting an id silences exactly that id ---
    Invoke-Tool @('-Path', $main, '-Mute', 'k7ax')
    Assert-True 'muted: exit 0' ($RC -eq 0)
    Assert-Eq 'muted: stderr empty' '' $ERR
    Assert-Eq 'muted: tally' $mutedExpected $OUT

    # --- a plain feed tallies per id ---
    $plain = Write-Fixture 'plain.csv' $plainCsv
    Invoke-Tool @('-Path', $plain)
    Assert-True 'plain: exit 0' ($RC -eq 0)
    Assert-Eq 'plain: tally' $plainExpected $OUT

    # --- missing feed ---
    $gone = Join-Path $T 'gone.csv'
    Invoke-Tool @('-Path', $gone)
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "agenttally: feed not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
