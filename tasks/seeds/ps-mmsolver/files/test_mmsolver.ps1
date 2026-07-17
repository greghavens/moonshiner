# Acceptance harness for mmsolver.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_mmsolver.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'mmsolver.ps1') -PathType Leaf)) {
    Write-Output 'FAIL mmsolver.ps1 not found in the workspace root'
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
function Invoke-Solver {
    param([string[]]$CaseArgs = @())
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'mmsolver.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- secret validation ---
    foreach ($bad in @('123', '12345', '1270', '7777', '1a12', 'abcd')) {
        Invoke-Solver @('-Secret', $bad)
        Assert-True "bad secret ${bad}: exit 2" ($RC -eq 2)
        Assert-Eq "bad secret ${bad}: stderr" "error: secret must be 4 digits 1-6`n" $ERR
        Assert-Eq "bad secret ${bad}: no stdout" '' $OUT
    }
    Invoke-Solver @()
    Assert-True 'missing secret: exit 2' ($RC -eq 2)
    Assert-Eq 'missing secret: stderr' "error: secret must be 4 digits 1-6`n" $ERR

    # --- pinned full transcripts ---
    Invoke-Solver @('-Secret', '3456')
    Assert-True '3456: exit 0' ($RC -eq 0)
    Assert-Eq '3456: stderr empty' '' $ERR
    Assert-Eq '3456: transcript' @'
GUESS 1 1122 black=0 white=0 candidates=256
GUESS 2 3333 black=1 white=0 candidates=108
GUESS 3 3444 black=2 white=0 candidates=12
GUESS 4 3455 black=3 white=0 candidates=2
GUESS 5 3456 black=4 white=0 candidates=1
CRACKED 3456 in 5 guesses

'@.Replace("`r`n", "`n") $OUT

    Invoke-Solver @('-Secret', '1122')
    Assert-True '1122: exit 0' ($RC -eq 0)
    Assert-Eq '1122: transcript (singular guess)' @'
GUESS 1 1122 black=4 white=0 candidates=1
CRACKED 1122 in 1 guess

'@.Replace("`r`n", "`n") $OUT

    # duplicate-heavy feedback: 1122 vs secret 1112 is black=3 white=0
    Invoke-Solver @('-Secret', '1112')
    Assert-True '1112: exit 0' ($RC -eq 0)
    Assert-Eq '1112: transcript' @'
GUESS 1 1122 black=3 white=0 candidates=20
GUESS 2 1112 black=4 white=0 candidates=1
CRACKED 1112 in 2 guesses

'@.Replace("`r`n", "`n") $OUT

    # --- fixture secrets: pinned guess counts + transcript invariants ---
    foreach ($line in [System.IO.File]::ReadAllLines((Join-Path $PSScriptRoot 'fixtures/secrets.txt'))) {
        if ($line.Trim().Length -eq 0) { continue }
        $parts = $line.Split(' ')
        $secret = $parts[0]
        $wantCount = [int]$parts[1]

        Invoke-Solver @('-Secret', $secret)
        Assert-True "${secret}: exit 0" ($RC -eq 0)
        Assert-Eq "${secret}: stderr empty" '' $ERR

        $lines = $OUT.TrimEnd("`n").Split("`n")
        $guessLines = @($lines | Where-Object { $_.StartsWith('GUESS ', [System.StringComparison]::Ordinal) })
        Assert-True "${secret}: cracked in $wantCount guesses (got $($guessLines.Count))" ($guessLines.Count -eq $wantCount)

        $unit = if ($wantCount -eq 1) { 'guess' } else { 'guesses' }
        Assert-Eq "${secret}: final line" "CRACKED $secret in $wantCount $unit" $lines[-1]
        Assert-True "${secret}: opens with the pinned first guess" ($guessLines[0].StartsWith('GUESS 1 1122 ', [System.StringComparison]::Ordinal))

        $codes = @()
        $cands = @()
        foreach ($g in $guessLines) {
            $tok = $g.Split(' ')
            $codes += $tok[2]
            $cands += [int]($tok[5].Split('=')[1])
        }
        Assert-True "${secret}: last guess is the secret" ($codes[-1] -ceq $secret)
        Assert-True "${secret}: no repeated guesses" (($codes | Select-Object -Unique).Count -eq $codes.Count)
        $monotone = $true
        for ($i = 1; $i -lt $cands.Count; $i++) {
            if ($cands[$i] -gt $cands[$i - 1]) { $monotone = $false }
        }
        Assert-True "${secret}: candidate counts never grow" $monotone
        Assert-True "${secret}: solved down to one candidate" ($cands[-1] -eq 1)
    }
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
