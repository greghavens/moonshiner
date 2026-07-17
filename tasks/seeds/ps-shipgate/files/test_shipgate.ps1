# Acceptance harness for shipgate.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_shipgate.ps1
# The tool's whole interface is negative space: exit code + at most one
# stderr line, stdout always empty, success completely silent. Every case
# below pins all three.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'shipgate.ps1') -PathType Leaf)) {
    Write-Output 'FAIL shipgate.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'shipgate.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

# One assertion bundle per case: exit code, empty stdout, exact stderr.
function Assert-Case {
    param([string]$Label, [int]$Code, [string]$ErrLine)
    Assert-True "${Label}: exit $Code" ($RC -eq $Code)
    Assert-Eq "${Label}: stdout empty" '' $OUT
    if ($ErrLine -eq '') {
        Assert-Eq "${Label}: stderr empty" '' $ERR
    } else {
        Assert-Eq "${Label}: stderr line" ($ErrLine + "`n") $ERR
    }
}

function Write-Manifest {
    param([string]$Name, [string]$Content)
    $p = Join-Path $T $Name
    [System.IO.File]::WriteAllText($p, $Content + "`n")
    return $p
}

$usageLine = 'shipgate: usage: shipgate.ps1 -Manifest <file> -Depot <dir>'

try {
    New-Item -ItemType Directory -Force -Path (Join-Path $T 'depot/payload') > $null
    [System.IO.File]::WriteAllText((Join-Path $T 'depot/release notes.txt'), "v2 gate set`n")
    [System.IO.File]::WriteAllText((Join-Path $T 'depot/payload/core.bin'), "0123456789`n")
    [System.IO.File]::WriteAllText((Join-Path $T 'depot/manifest.sig'), "sig-ok`n")
    $depot = Join-Path $T 'depot'

    # --- the gate passes: dead silent ---
    $good = Write-Manifest 'good.manifest' @'
# gate set v2
12 release notes.txt
11 payload/core.bin

7 manifest.sig
'@
    Invoke-Tool @('-Manifest', $good, '-Depot', $depot)
    Assert-Case 'pass' 0 ''

    # --- a manifest of nothing but comments and blanks also passes silently ---
    $hollow = Write-Manifest 'hollow.manifest' @'
# nothing staged yet

# see you next release
'@
    Invoke-Tool @('-Manifest', $hollow, '-Depot', $depot)
    Assert-Case 'hollow pass' 0 ''

    # --- usage errors: no args / half the args / a stray extra arg ---
    Invoke-Tool @()
    Assert-Case 'no args' 64 $usageLine
    Invoke-Tool @('-Manifest', $good)
    Assert-Case 'manifest only' 64 $usageLine
    Invoke-Tool @('-Manifest', $good, '-Depot', $depot, 'stray')
    Assert-Case 'stray arg' 64 $usageLine

    # --- missing inputs ---
    $nope = Join-Path $T 'nope.manifest'
    Invoke-Tool @('-Manifest', $nope, '-Depot', $depot)
    Assert-Case 'no manifest' 66 "shipgate: no such manifest: $nope"
    $nodepot = Join-Path $T 'nodepot'
    Invoke-Tool @('-Manifest', $good, '-Depot', $nodepot)
    Assert-Case 'no depot' 66 "shipgate: no such depot: $nodepot"

    # --- malformed manifest lines (line numbers count every line) ---
    $mal = Write-Manifest 'malformed.manifest' @'
# gate set v2
12 release notes.txt
oversize widget.bin
7 manifest.sig
'@
    Invoke-Tool @('-Manifest', $mal, '-Depot', $depot)
    Assert-Case 'word size' 65 "shipgate: manifest line 3: expected '<size> <relpath>'"

    $glued = Write-Manifest 'glued.manifest' @'
12 release notes.txt
12x manifest.sig
'@
    Invoke-Tool @('-Manifest', $glued, '-Depot', $depot)
    Assert-Case 'glued size' 65 "shipgate: manifest line 2: expected '<size> <relpath>'"

    $dup = Write-Manifest 'dup.manifest' @'
12 release notes.txt
11 payload/core.bin
12 release notes.txt
'@
    Invoke-Tool @('-Manifest', $dup, '-Depot', $depot)
    Assert-Case 'duplicate' 65 'shipgate: manifest line 3: duplicate entry: release notes.txt'

    # --- gate failures ---
    $ghost = Write-Manifest 'ghost.manifest' @'
12 release notes.txt
5 payload/ghost.bin
7 manifest.sig
'@
    Invoke-Tool @('-Manifest', $ghost, '-Depot', $depot)
    Assert-Case 'missing file' 70 'shipgate: gate failed: missing payload/ghost.bin'

    $badsize = Write-Manifest 'badsize.manifest' @'
13 release notes.txt
11 payload/core.bin
'@
    Invoke-Tool @('-Manifest', $badsize, '-Depot', $depot)
    Assert-Case 'size mismatch' 70 'shipgate: gate failed: size release notes.txt expected 13 got 12'

    # --- the whole manifest parses before the depot is checked: a bad line
    # --- after a failing entry still reports as a 65, not a 70 ---
    $prec = Write-Manifest 'precedence.manifest' @'
13 release notes.txt
11 payload/core.bin
12x manifest.sig
'@
    Invoke-Tool @('-Manifest', $prec, '-Depot', $depot)
    Assert-Case 'parse precedence' 65 "shipgate: manifest line 3: expected '<size> <relpath>'"

    # --- among gate failures, the first failing entry in manifest order wins ---
    $order = Write-Manifest 'order.manifest' @'
7 manifest.sig
5 payload/ghost.bin
13 release notes.txt
'@
    Invoke-Tool @('-Manifest', $order, '-Depot', $depot)
    Assert-Case 'first failure wins' 70 'shipgate: gate failed: missing payload/ghost.bin'
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
