# Acceptance harness for bingo.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_bingo.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'bingo.ps1') -PathType Leaf)) {
    Write-Output 'FAIL bingo.ps1 not found in the workspace root'
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
function Invoke-Bingo {
    param([string[]]$CaseArgs = @())
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'bingo.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function Write-Calls {
    param([string]$Name, [string[]]$Lines)
    $path = Join-Path $T $Name
    [System.IO.File]::WriteAllText($path, (($Lines -join "`n") + "`n"))
    return $path
}

$ALPHA = Join-Path $PSScriptRoot 'fixtures/alpha.card'
$BETA = Join-Path $PSScriptRoot 'fixtures/beta.card'
$GAMMA = Join-Path $PSScriptRoot 'fixtures/gamma.card'

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- CLI validation ---
    Invoke-Bingo @()
    Assert-True 'no args: exit 2' ($RC -eq 2)
    Assert-Eq 'no args: usage' "usage: bingo.ps1 -Cards <card,...> -Calls <file>`n" $ERR

    $calls = Write-Calls 'ok.calls' @('B1')
    Invoke-Bingo @('-Cards', (Join-Path $T 'ghost.card'), '-Calls', $calls)
    Assert-True 'missing card file: exit 2' ($RC -eq 2)
    Assert-Eq 'missing card file: message' "error: cannot read $(Join-Path $T 'ghost.card')`n" $ERR

    Invoke-Bingo @('-Cards', $ALPHA, '-Calls', (Join-Path $T 'ghost.calls'))
    Assert-True 'missing calls file: exit 2' ($RC -eq 2)
    Assert-Eq 'missing calls file: message' "error: cannot read $(Join-Path $T 'ghost.calls')`n" $ERR

    # --- card validation (bad cards are authored into _t) ---
    $bad = Join-Path $T 'short.card'
    [System.IO.File]::WriteAllText($bad, "1 16 31 46 61`n2 17 32 47`n3 18 * 48 63`n4 19 33 49 64`n5 20 34 50 65`n")
    Invoke-Bingo @('-Cards', $bad, '-Calls', $calls)
    Assert-True 'short row: exit 2' ($RC -eq 2)
    Assert-Eq 'short row: message' "error: short: bad card line 2: 2 17 32 47`n" $ERR

    $bad = Join-Path $T 'range.card'
    [System.IO.File]::WriteAllText($bad, "1 16 31 46 61`n16 17 32 47 62`n3 18 * 48 63`n4 19 33 49 64`n5 20 34 50 65`n")
    Invoke-Bingo @('-Cards', $bad, '-Calls', $calls)
    Assert-True 'out-of-range B column: exit 2' ($RC -eq 2)
    Assert-Eq 'out-of-range B column: message' "error: range: bad card line 2: 16 17 32 47 62`n" $ERR

    $bad = Join-Path $T 'nocenter.card'
    [System.IO.File]::WriteAllText($bad, "1 16 31 46 61`n2 17 32 47 62`n3 18 45 48 63`n4 19 33 49 64`n5 20 34 50 65`n")
    Invoke-Bingo @('-Cards', $bad, '-Calls', $calls)
    Assert-True 'missing free center: exit 2' ($RC -eq 2)
    Assert-Eq 'missing free center: message' "error: nocenter: bad card line 3: 3 18 45 48 63`n" $ERR

    $bad = Join-Path $T 'strayfree.card'
    [System.IO.File]::WriteAllText($bad, "1 * 31 46 61`n2 17 32 47 62`n3 18 * 48 63`n4 19 33 49 64`n5 20 34 50 65`n")
    Invoke-Bingo @('-Cards', $bad, '-Calls', $calls)
    Assert-True 'stray free cell: exit 2' ($RC -eq 2)
    Assert-Eq 'stray free cell: message' "error: strayfree: bad card line 1: 1 * 31 46 61`n" $ERR

    $bad = Join-Path $T 'dup.card'
    [System.IO.File]::WriteAllText($bad, "1 16 31 46 61`n2 17 32 47 62`n3 18 * 48 63`n4 19 33 49 64`n1 20 34 50 65`n")
    Invoke-Bingo @('-Cards', $bad, '-Calls', $calls)
    Assert-True 'duplicate number: exit 2' ($RC -eq 2)
    Assert-Eq 'duplicate number: message' "error: dup: duplicate number 1`n" $ERR

    # --- call validation ---
    $calls = Write-Calls 'badletter.calls' @('B1', 'Q7')
    Invoke-Bingo @('-Cards', $ALPHA, '-Calls', $calls)
    Assert-True 'bad call letter: exit 2' ($RC -eq 2)
    Assert-Eq 'bad call letter: message' "error: bad call line 2: Q7`n" $ERR
    Assert-Eq 'bad call letter: validated before play' '' $OUT

    $calls = Write-Calls 'badrange.calls' @('B16')
    Invoke-Bingo @('-Cards', $ALPHA, '-Calls', $calls)
    Assert-True 'call out of letter range: exit 2' ($RC -eq 2)
    Assert-Eq 'call out of letter range: message' "error: bad call line 1: B16`n" $ERR

    $calls = Write-Calls 'dupcall.calls' @('B1', 'I17', 'B1')
    Invoke-Bingo @('-Cards', $ALPHA, '-Calls', $calls)
    Assert-True 'duplicate call: exit 2' ($RC -eq 2)
    Assert-Eq 'duplicate call: message' "error: duplicate call B1`n" $ERR

    # --- row win across three cards, with decoy daubs ---
    $calls = Write-Calls 'row.calls' @('B3', 'N35', 'I18', 'O70', 'G48', 'O63')
    Invoke-Bingo @('-Cards', "$ALPHA,$BETA,$GAMMA", '-Calls', $calls)
    Assert-True 'row win: exit 0' ($RC -eq 0)
    Assert-Eq 'row win: stderr empty' '' $ERR
    Assert-Eq 'row win: transcript' @'
CARD alpha ok
CARD beta ok
CARD gamma ok
CALL 1 B3
DAUB alpha R3C1
CALL 2 N35
DAUB beta R1C3
CALL 3 I18
DAUB alpha R3C2
CALL 4 O70
DAUB beta R5C5
CALL 5 G48
DAUB alpha R3C4
CALL 6 O63
DAUB alpha R3C5
BINGO alpha R3
GAME OVER after 6 calls

'@.Replace("`r`n", "`n") $OUT

    # --- column win, single card ---
    $calls = Write-Calls 'col.calls' @('B6', 'B7', 'B8', 'B9', 'B10')
    Invoke-Bingo @('-Cards', $BETA, '-Calls', $calls)
    Assert-Eq 'column win: transcript' @'
CARD beta ok
CALL 1 B6
DAUB beta R1C1
CALL 2 B7
DAUB beta R2C1
CALL 3 B8
DAUB beta R3C1
CALL 4 B9
DAUB beta R4C1
CALL 5 B10
DAUB beta R5C1
BINGO beta C1
GAME OVER after 5 calls

'@.Replace("`r`n", "`n") $OUT

    # --- main diagonal through the free center; shared numbers daub both cards ---
    $calls = Write-Calls 'diag.calls' @('B1', 'I17', 'G49', 'O65')
    Invoke-Bingo @('-Cards', "$ALPHA,$GAMMA", '-Calls', $calls)
    Assert-Eq 'diagonal win: transcript' @'
CARD alpha ok
CARD gamma ok
CALL 1 B1
DAUB alpha R1C1
DAUB gamma R1C1
CALL 2 I17
DAUB alpha R2C2
DAUB gamma R2C2
CALL 3 G49
DAUB alpha R4C4
CALL 4 O65
DAUB alpha R5C5
DAUB gamma R5C5
BINGO alpha D1
GAME OVER after 4 calls

'@.Replace("`r`n", "`n") $OUT

    # --- anti-diagonal ---
    $calls = Write-Calls 'anti.calls' @('O66', 'G52', 'I24', 'B10')
    Invoke-Bingo @('-Cards', $BETA, '-Calls', $calls)
    Assert-Eq 'anti-diagonal win: transcript' @'
CARD beta ok
CALL 1 O66
DAUB beta R1C5
CALL 2 G52
DAUB beta R2C4
CALL 3 I24
DAUB beta R4C2
CALL 4 B10
DAUB beta R5C1
BINGO beta D2
GAME OVER after 4 calls

'@.Replace("`r`n", "`n") $OUT

    # --- four corners: two cards win the same round, announced in card order ---
    $calls = Write-Calls 'corners.calls' @('B1', 'O61', 'B5', 'O65')
    Invoke-Bingo @('-Cards', "$ALPHA,$BETA,$GAMMA", '-Calls', $calls)
    Assert-Eq 'corners double win: transcript' @'
CARD alpha ok
CARD beta ok
CARD gamma ok
CALL 1 B1
DAUB alpha R1C1
DAUB gamma R1C1
CALL 2 O61
DAUB alpha R1C5
DAUB gamma R1C5
CALL 3 B5
DAUB alpha R5C1
DAUB gamma R5C1
CALL 4 O65
DAUB alpha R5C5
DAUB gamma R5C5
BINGO alpha CORNERS
BINGO gamma CORNERS
GAME OVER after 4 calls

'@.Replace("`r`n", "`n") $OUT

    # --- one call completes a row AND a column: the row outranks it ---
    $calls = Write-Calls 'prio.calls' @('B2', 'N32', 'G47', 'O62', 'I16', 'I18', 'I19', 'I20', 'I17')
    Invoke-Bingo @('-Cards', $ALPHA, '-Calls', $calls)
    Assert-Eq 'priority row over column: transcript' @'
CARD alpha ok
CALL 1 B2
DAUB alpha R2C1
CALL 2 N32
DAUB alpha R2C3
CALL 3 G47
DAUB alpha R2C4
CALL 4 O62
DAUB alpha R2C5
CALL 5 I16
DAUB alpha R1C2
CALL 6 I18
DAUB alpha R3C2
CALL 7 I19
DAUB alpha R4C2
CALL 8 I20
DAUB alpha R5C2
CALL 9 I17
DAUB alpha R2C2
BINGO alpha R2
GAME OVER after 9 calls

'@.Replace("`r`n", "`n") $OUT

    # --- calls exhausted with no winner; comments, blanks, dud calls ---
    $calls = Write-Calls 'none.calls' @('# night one', 'B1', '', 'I22', 'N33', 'G54', 'N45', 'O65')
    Invoke-Bingo @('-Cards', "$ALPHA,$BETA", '-Calls', $calls)
    Assert-True 'no winner: exit 0' ($RC -eq 0)
    Assert-Eq 'no winner: transcript' @'
CARD alpha ok
CARD beta ok
CALL 1 B1
DAUB alpha R1C1
CALL 2 I22
DAUB beta R2C2
CALL 3 N33
DAUB alpha R4C3
CALL 4 G54
DAUB beta R4C4
CALL 5 N45
CALL 6 O65
DAUB alpha R5C5
NO BINGO after 6 calls

'@.Replace("`r`n", "`n") $OUT
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
