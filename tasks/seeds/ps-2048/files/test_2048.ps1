# Acceptance harness for play2048.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_2048.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'play2048.ps1') -PathType Leaf)) {
    Write-Output 'FAIL play2048.ps1 not found in the workspace root'
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
function Invoke-Game {
    param([string[]]$CaseArgs = @())
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'play2048.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function Write-Moves {
    param([string]$Name, [string[]]$Moves)
    $path = Join-Path $T $Name
    [System.IO.File]::WriteAllText($path, (($Moves -join "`n") + "`n"))
    return $path
}

function Fixture([string]$Name) { return (Join-Path $PSScriptRoot "fixtures/$Name") }

try {
    New-Item -ItemType Directory -Force -Path $T > $null
    $empty = Write-Moves 'empty.moves' @()
    [System.IO.File]::WriteAllText($empty, '')

    # --- CLI validation ---
    Invoke-Game @()
    Assert-True 'no -Moves: exit 2' ($RC -eq 2)
    Assert-Eq 'no -Moves: usage' "usage: play2048.ps1 -Moves <file> [-Seed <n>] [-Board <file>]`n" $ERR

    Invoke-Game @('-Moves', (Join-Path $T 'nope.moves'), '-Seed', '1')
    Assert-True 'missing moves file: exit 2' ($RC -eq 2)
    Assert-Eq 'missing moves file: message' "error: cannot read $(Join-Path $T 'nope.moves')`n" $ERR

    Invoke-Game @('-Moves', $empty, '-Seed', 'abc')
    Assert-True 'bad seed: exit 2' ($RC -eq 2)
    Assert-Eq 'bad seed: message' "error: seed must be a non-negative integer`n" $ERR

    $badMoves = Write-Moves 'bad.moves' @('L', 'X', 'R')
    Invoke-Game @('-Moves', $badMoves, '-Seed', '1')
    Assert-True 'bad move: exit 2' ($RC -eq 2)
    Assert-Eq 'bad move: message' "error: bad move line 2: X`n" $ERR
    Assert-Eq 'bad move: no transcript before validation' '' $OUT

    $badBoard = Join-Path $T 'bad.board'
    [System.IO.File]::WriteAllText($badBoard, "2 4 8 16`n. . . .`n2 3 . .`n. . . .`n")
    Invoke-Game @('-Moves', $empty, '-Board', $badBoard)
    Assert-True 'bad board: exit 2' ($RC -eq 2)
    Assert-Eq 'bad board: message' "error: bad board line 3: 2 3 . .`n" $ERR

    Invoke-Game @('-Moves', $empty, '-Board', (Join-Path $T 'ghost.board'))
    Assert-True 'missing board: exit 2' ($RC -eq 2)
    Assert-Eq 'missing board: message' "error: cannot read $(Join-Path $T 'ghost.board')`n" $ERR

    # --- LCG-driven initial spawns ---
    Invoke-Game @('-Moves', $empty, '-Seed', '1')
    Assert-True 'seed 1: exit 0' ($RC -eq 0)
    Assert-Eq 'seed 1: stderr empty' '' $ERR
    Assert-Eq 'seed 1: INIT block' @'
INIT score=0
. . . 2
. . 2 .
. . . .
. . . .
END score=0

'@.Replace("`r`n", "`n") $OUT

    Invoke-Game @('-Moves', $empty, '-Seed', '10')
    Assert-Eq 'seed 10: second spawn is a 4' @'
INIT score=0
. . . .
. . . .
. . 4 .
. . . 2
END score=0

'@.Replace("`r`n", "`n") $OUT

    # --- merge law, one direction per run over the same fixture board ---
    $mv = Write-Moves 'L.moves' @('L')
    Invoke-Game @('-Moves', $mv, '-Seed', '5', '-Board', (Fixture 'rows.board'))
    Assert-True 'rows L: exit 0' ($RC -eq 0)
    Assert-Eq 'rows L: transcript' @'
INIT score=0
2 2 2 2
2 2 4 8
4 2 2 .
2 . 2 4
MOVE L gained=20 score=20 spawn=0,2=2
4 4 2 .
4 4 8 .
4 4 . .
4 4 . .
END score=20

'@.Replace("`r`n", "`n") $OUT

    $mv = Write-Moves 'R.moves' @('R')
    Invoke-Game @('-Moves', $mv, '-Seed', '5', '-Board', (Fixture 'rows.board'))
    Assert-Eq 'rows R: transcript' @'
INIT score=0
2 2 2 2
2 2 4 8
4 2 2 .
2 . 2 4
MOVE R gained=20 score=20 spawn=0,0=2
2 . 4 4
. 4 4 8
. . 4 4
. . 4 4
END score=20

'@.Replace("`r`n", "`n") $OUT

    $mv = Write-Moves 'U.moves' @('U')
    Invoke-Game @('-Moves', $mv, '-Seed', '7', '-Board', (Fixture 'rows.board'))
    Assert-Eq 'rows U: transcript' @'
INIT score=0
2 2 2 2
2 2 4 8
4 2 2 .
2 . 2 4
MOVE U gained=12 score=12 spawn=3,3=2
4 4 2 2
4 2 4 8
2 . 4 4
. . . 2
END score=12

'@.Replace("`r`n", "`n") $OUT

    $mv = Write-Moves 'D.moves' @('D')
    Invoke-Game @('-Moves', $mv, '-Seed', '7', '-Board', (Fixture 'rows.board'))
    Assert-Eq 'rows D: transcript' @'
INIT score=0
2 2 2 2
2 2 4 8
4 2 2 .
2 . 2 4
MOVE D gained=12 score=12 spawn=1,1=2
. . . .
4 2 2 2
4 2 4 8
2 4 4 4
END score=12

'@.Replace("`r`n", "`n") $OUT

    # --- rejected moves cost nothing (no spawn, no LCG draw) ---
    $mv = Write-Moves 'LD.moves' @('L', '# slide them down instead', '', 'D')
    Invoke-Game @('-Moves', $mv, '-Seed', '3', '-Board', (Fixture 'packed.board'))
    Assert-Eq 'packed L then D: transcript' @'
INIT score=0
2 4 8 16
. . . .
. . . .
2 4 8 16
MOVE L rejected
MOVE D gained=60 score=60 spawn=2,3=2
. . . .
. . . .
. . . 2
4 8 16 32
END score=60

'@.Replace("`r`n", "`n") $OUT

    # --- reaching 2048 announces WIN exactly once ---
    $mv = Write-Moves 'winU.moves' @('U')
    Invoke-Game @('-Moves', $mv, '-Seed', '2', '-Board', (Fixture 'win.board'))
    Assert-Eq 'win: transcript' @'
INIT score=0
1024 8 . .
1024 2 . .
. . . .
. . . .
MOVE U gained=2048 score=2048 spawn=3,2=2
2048 8 . .
. 2 . .
. . . .
. . 2 .
WIN
END score=2048

'@.Replace("`r`n", "`n") $OUT

    # --- a board with no legal move is over before any input is read ---
    $mv = Write-Moves 'deadL.moves' @('L')
    Invoke-Game @('-Moves', $mv, '-Seed', '9', '-Board', (Fixture 'dead.board'))
    Assert-True 'dead: exit 0' ($RC -eq 0)
    Assert-Eq 'dead: transcript' @'
INIT score=0
2 4 2 4
4 2 4 2
2 4 2 4
4 2 4 2
GAME OVER score=0

'@.Replace("`r`n", "`n") $OUT

    # --- full pinned game from seed-74 spawns to GAME OVER mid-script ---
    Invoke-Game @('-Moves', (Fixture 'long_moves.txt'), '-Seed', '74')
    Assert-True 'long game: exit 0' ($RC -eq 0)
    Assert-Eq 'long game: stderr empty' '' $ERR
    $want = [System.IO.File]::ReadAllText((Fixture 'long_expected.txt'))
    Assert-Eq 'long game: full transcript' $want $OUT
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
