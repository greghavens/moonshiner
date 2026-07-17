# Acceptance harness for walkplan.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_walkplan.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$tool = Join-Path $PSScriptRoot 'walkplan.ps1'
if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
    Write-Output 'FAIL walkplan.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'walkplan.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

# System.IO APIs bind literally, so the harness itself is immune to the
# wildcard problem the tool has to solve.
function New-Fix {
    param([string]$Base, [string]$Rel, [string]$Content)
    $p = Join-Path $Base $Rel
    [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($p)) > $null
    [System.IO.File]::WriteAllText($p, $Content)
}

$expected = @'
delete 3 -lead dash.tmp
compress 7 .hidden.log
review 2 .stash
compress 8 UPPER.LOG
compress 10 app.log
keep 12 café config.cfg
review 22 note1.txt
review 5 note[1].txt
delete 9 old [2024]/archive notes.bak
compress 4 old [2024]/rollup.log
review 6 readme

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null
    $scanRoot = Join-Path $T 'scan [prod] A'
    New-Fix $scanRoot 'app.log' '0123456789'
    New-Fix $scanRoot '-lead dash.tmp' 'abc'
    New-Fix $scanRoot '.hidden.log' 'hidden!'
    New-Fix $scanRoot '.stash' 'st'
    New-Fix $scanRoot 'UPPER.LOG' 'UPCASED!'
    New-Fix $scanRoot 'café config.cfg' 'cfg contents'
    New-Fix $scanRoot 'note[1].txt' 'five!'
    New-Fix $scanRoot 'note1.txt' 'twenty-two bytes here!'
    New-Fix $scanRoot 'readme' 'readme'
    New-Fix (Join-Path $scanRoot 'old [2024]') 'archive notes.bak' 'nine byte'
    New-Fix (Join-Path $scanRoot 'old [2024]') 'rollup.log' 'roll'

    # --- absolute root whose own path carries spaces and brackets ---
    Invoke-Tool @('-Root', $scanRoot)
    Assert-True 'abs: exit 0' ($RC -eq 0)
    Assert-Eq 'abs: stderr empty' '' $ERR
    Assert-Eq 'abs: inventory' $expected $OUT

    # --- same root passed as a relative path ---
    Invoke-Tool @('-Root', (Join-Path '_t' 'scan [prod] A'))
    Assert-True 'rel: exit 0' ($RC -eq 0)
    Assert-Eq 'rel: stderr empty' '' $ERR
    Assert-Eq 'rel: inventory' $expected $OUT

    # --- a directory with no files at all ---
    $emptyRoot = Join-Path $T 'empty [dir]'
    [System.IO.Directory]::CreateDirectory($emptyRoot) > $null
    Invoke-Tool @('-Root', $emptyRoot)
    Assert-True 'empty: exit 0' ($RC -eq 0)
    Assert-Eq 'empty: no output' '' $OUT
    Assert-Eq 'empty: stderr empty' '' $ERR

    # --- missing root ---
    $gone = Join-Path $T 'no such [root]'
    Invoke-Tool @('-Root', $gone)
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "walkplan: root not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
