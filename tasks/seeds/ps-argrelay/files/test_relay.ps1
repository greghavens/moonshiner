# Acceptance harness for relay.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_relay.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'relay.ps1') -PathType Leaf)) {
    Write-Output 'FAIL relay.ps1 not found in the workspace root'
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
function Invoke-Relay {
    param([string[]]$CaseArgs = @(), [string]$WorkDir = $PSScriptRoot)
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    Push-Location -LiteralPath $WorkDir
    try {
        & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'relay.ps1') @CaseArgs 1>$outFile 2>$errFile
        $script:RC = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function Manifest {
    param([string[]]$Values)
    $lines = @("argc=$($Values.Count)")
    for ($i = 0; $i -lt $Values.Count; $i++) {
        $lines += ('[{0}] {1}:{2}' -f $i, $Values[$i].Length, $Values[$i])
    }
    return (($lines -join "`n") + "`n")
}

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # Child scripts the relay will be pointed at. echoargs prints the same
    # manifest shape the relay records, so the round trip is comparable.
    $echoArgs = Join-Path $T 'echoargs.ps1'
    [System.IO.File]::WriteAllText($echoArgs, @'
$lines = @("argc=$($args.Count)")
for ($i = 0; $i -lt $args.Count; $i++) {
    $a = [string]$args[$i]
    $lines += ('[{0}] {1}:{2}' -f $i, $a.Length, $a)
}
Write-Output ($lines -join "`n")
exit 0
'@)

    $grumbler = Join-Path $T 'grumbler.ps1'
    [System.IO.File]::WriteAllText($grumbler, @'
Write-Output "grumbler saw $($args.Count) args"
[Console]::Error.WriteLine('grumbler: refusing today')
exit 9
'@)

    # --- round trip: spaces, an empty string, a switch-looking token ---
    $rep1 = Join-Path $T 'rep1.txt'
    $fwd1 = @('hotfix for cart totals', '', '-Verbose')
    Invoke-Relay (@($rep1, $echoArgs) + $fwd1)
    Assert-True 'roundtrip: exit 0' ($RC -eq 0)
    Assert-Eq 'roundtrip: child saw exactly what we sent' (Manifest $fwd1) $OUT
    Assert-Eq 'roundtrip: report' ((Manifest $fwd1) + "exit=0`n") ([System.IO.File]::ReadAllText($rep1))
    Assert-Eq 'roundtrip: stderr empty' '' $ERR

    # --- nothing to forward at all ---
    $rep2 = Join-Path $T 'rep2.txt'
    Invoke-Relay @($rep2, $echoArgs)
    Assert-True 'empty: exit 0' ($RC -eq 0)
    Assert-Eq 'empty: child manifest' "argc=0`n" $OUT
    Assert-Eq 'empty: report' "argc=0`nexit=0`n" ([System.IO.File]::ReadAllText($rep2))

    # --- the gnarly set: dashes, a lone dash, a glob, a zero, padded spaces ---
    $rep3 = Join-Path $T 'rep3.txt'
    $fwd3 = @('--target=web1', '-', 'notes/*.md', '0', '  pad  ', 'a b  c')
    Invoke-Relay (@($rep3, $echoArgs) + $fwd3)
    Assert-True 'gnarly: exit 0' ($RC -eq 0)
    Assert-Eq 'gnarly: child saw exactly what we sent' (Manifest $fwd3) $OUT
    Assert-Eq 'gnarly: report' ((Manifest $fwd3) + "exit=0`n") ([System.IO.File]::ReadAllText($rep3))

    # --- consecutive empty strings survive ---
    $rep4 = Join-Path $T 'rep4.txt'
    $fwd4 = @('', '', 'tail')
    Invoke-Relay (@($rep4, $echoArgs) + $fwd4)
    Assert-Eq 'empties: child saw exactly what we sent' (Manifest $fwd4) $OUT
    Assert-Eq 'empties: report' ((Manifest $fwd4) + "exit=0`n") ([System.IO.File]::ReadAllText($rep4))

    # --- child streams pass through and its exit code is the relay's ---
    $rep5 = Join-Path $T 'rep5.txt'
    Invoke-Relay @($rep5, $grumbler, 'x y')
    Assert-True 'grumbler: exit 9' ($RC -eq 9)
    Assert-Eq 'grumbler: stdout passthrough' "grumbler saw 1 args`n" $OUT
    Assert-Contains 'grumbler: stderr passthrough' 'grumbler: refusing today' $ERR
    Assert-Eq 'grumbler: report records the exit' ((Manifest @('x y')) + "exit=9`n") ([System.IO.File]::ReadAllText($rep5))

    # --- a relative report path lands in the invoker's working directory ---
    Invoke-Relay @('rel-report.txt', $echoArgs, 'one two') $T
    Assert-True 'relative report: exit 0' ($RC -eq 0)
    Assert-True 'relative report: file created where we ran' (Test-Path -LiteralPath (Join-Path $T 'rel-report.txt') -PathType Leaf)
    Assert-Eq 'relative report: content' ((Manifest @('one two')) + "exit=0`n") ([System.IO.File]::ReadAllText((Join-Path $T 'rel-report.txt')))

    # --- usage errors ---
    Invoke-Relay @()
    Assert-True 'no args: exit 64' ($RC -eq 64)
    Assert-Eq 'no args: stdout empty' '' $OUT
    Assert-Eq 'no args: usage line' "relay: usage: relay.ps1 <report> <child.ps1> [args...]`n" $ERR

    $repOnly = Join-Path $T 'rep-only.txt'
    Invoke-Relay @($repOnly)
    Assert-True 'one arg: exit 64' ($RC -eq 64)
    Assert-Eq 'one arg: usage line' "relay: usage: relay.ps1 <report> <child.ps1> [args...]`n" $ERR
    Assert-True 'one arg: no report written' (-not (Test-Path -LiteralPath $repOnly))

    # --- missing child: refuse before touching the report file ---
    $repGone = Join-Path $T 'rep-gone.txt'
    $ghost = Join-Path $T 'ghost.ps1'
    Invoke-Relay @($repGone, $ghost, 'a', 'b')
    Assert-True 'missing child: exit 65' ($RC -eq 65)
    Assert-Eq 'missing child: message' "relay: no such script: $ghost`n" $ERR
    Assert-True 'missing child: no report written' (-not (Test-Path -LiteralPath $repGone))
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
