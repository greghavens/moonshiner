# Acceptance harness for duroll.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_duroll.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$tool = Join-Path $PSScriptRoot 'duroll.ps1'
if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
    Write-Output 'FAIL duroll.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'duroll.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function New-Sized {
    param([string]$Base, [string]$Rel, [int]$Bytes)
    $p = Join-Path $Base $Rel
    [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($p)) > $null
    [System.IO.File]::WriteAllBytes($p, [byte[]]::new($Bytes))
}

# vol totals: . = 1052479, media = 1048575 (the just-under-a-MiB edge),
# logs = 1024 own + 1500 below, logs/archive = 1500, stash = 1280,
# media/raw = 0
$volTop5 = @'
. 1.0 MiB
media 1024.0 KiB
logs 2.5 KiB
logs/archive 1.5 KiB
stash 1.3 KiB

'@

$volTop3 = @'
. 1.0 MiB
media 1024.0 KiB
logs 2.5 KiB

'@

$volAll = @'
. 1.0 MiB
media 1024.0 KiB
logs 2.5 KiB
logs/archive 1.5 KiB
stash 1.3 KiB
media/raw 0 B

'@

$tieAll = @'
. 1.5 KiB
B 512 B
a 512 B
b 512 B

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    $vol = Join-Path $T 'vol'
    New-Sized $vol 'notes.txt' 100
    New-Sized $vol 'logs/app.log' 600
    New-Sized $vol 'logs/.audit' 424
    New-Sized $vol 'logs/archive/jan.log' 1000
    New-Sized $vol 'logs/archive/feb.log' 500
    New-Sized $vol 'media/clip.bin' 1048575
    [System.IO.Directory]::CreateDirectory((Join-Path $vol 'media/raw')) > $null
    New-Sized $vol 'stash/a.dat' 1280

    # --- default Top is 5 ---
    Invoke-Tool @('-Root', $vol)
    Assert-True 'default: exit 0' ($RC -eq 0)
    Assert-Eq 'default: stderr empty' '' $ERR
    Assert-Eq 'default: top 5' $volTop5 $OUT

    # --- explicit -Top 3 ---
    Invoke-Tool @('-Root', $vol, '-Top', '3')
    Assert-True 'top3: exit 0' ($RC -eq 0)
    Assert-Eq 'top3: report' $volTop3 $OUT

    # --- -Top larger than the directory count prints everything ---
    Invoke-Tool @('-Root', $vol, '-Top', '100')
    Assert-True 'topall: exit 0' ($RC -eq 0)
    Assert-Eq 'topall: report' $volAll $OUT

    # --- equal sizes break ties by ordinal path ---
    $tie = Join-Path $T 'tie'
    New-Sized $tie 'b/x.dat' 512
    New-Sized $tie 'a/y.dat' 512
    New-Sized $tie 'B/z.dat' 512
    Invoke-Tool @('-Root', $tie, '-Top', '10')
    Assert-True 'tie: exit 0' ($RC -eq 0)
    Assert-Eq 'tie: report' $tieAll $OUT

    # --- -Top must be positive ---
    Invoke-Tool @('-Root', $vol, '-Top', '0')
    Assert-True 'topzero: nonzero exit' ($RC -ne 0)
    Assert-Eq 'topzero: stdout empty' '' $OUT

    # --- missing root ---
    $gone = Join-Path $T 'gone'
    Invoke-Tool @('-Root', $gone)
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "duroll: root not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
