# Acceptance harness for briefkit.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_briefkit.ps1
#
# FEASIBILITY GATE (this is the perf contract): the big case below feeds
# Build-ShiftBrief a 500,000-line log. A linear implementation (collect lines,
# join once — or a StringBuilder) renders it in well under 10 seconds on this
# class of machine; the quadratic prototype that grew the document with
# `$doc += $line` in the loop was measured at 0.93s for 20k lines and 5.7s for
# 40k lines, which extrapolates to ~15 MINUTES at 500k (and in practice gets
# worse as the working string passes 20 MB). The verify timeout is 180
# seconds, so the quadratic build cannot finish inside it while the intended
# implementation clears the whole harness with more than 20x headroom.
# There are deliberately no wall-clock assertions in this file.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$lib = Join-Path $PSScriptRoot 'briefkit.ps1'
if (-not (Test-Path -LiteralPath $lib -PathType Leaf)) {
    Write-Output 'FAIL briefkit.ps1 not found in the workspace root'
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

# All behavior checks run inside ONE child pwsh that dot-sources the library
# and prints a labeled block; the block is compared byte-exact. The big case
# is checked by length + SHA-256 instead of printing 21 MB.
$driver = @'
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'briefkit.ps1')

function Out-Doc {
    param([string]$Label, [string]$Name)
    $r = @(Build-ShiftBrief -Path (Join-Path $PSScriptRoot $Name))
    Write-Output "--- $Label count=$($r.Count) type=$($r[0].GetType().Name) ---"
    [Console]::Out.Write([string]$r[0])
}

Out-Doc 'small' 'small.log'
Out-Doc 'empty' 'empty.log'

try {
    $null = Build-ShiftBrief -Path (Join-Path $PSScriptRoot 'no-such.log')
    Write-Output '--- missing NO-ERROR ---'
} catch {
    Write-Output "--- missing ERR $($_.Exception.Message) ---"
}

$big = @(Build-ShiftBrief -Path (Join-Path $PSScriptRoot 'big.log'))
$doc = [string]$big[0]
$sha = [System.Security.Cryptography.SHA256]::Create()
$hash = [System.BitConverter]::ToString($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($doc))).Replace('-', '').ToLowerInvariant()
Write-Output "--- big count=$($big.Count) len=$($doc.Length) sha256=$hash ---"
'@

$smallLog = @'
ams kiosk-113 WARN 81 fan-high
fra kiosk-007 INFO 12 ok
sensor glitch dump 40331
zrh kiosk-113 warn 55 net-drop

fra kiosk-350 FAIL 803 disk-slow
ams kiosk-007 INFO 9 ok
ams  kiosk-002 INFO 4 ok
lis kiosk-118 WARN 77 fan-high
'@

$expected = @'
--- small count=1 type=String ---
shift brief
0000001 WARN  ams/kiosk-113 81ms fan-high
0000002 INFO  fra/kiosk-007 12ms ok
0000003 warn  zrh/kiosk-113 55ms net-drop
0000004 FAIL  fra/kiosk-350 803ms disk-slow
0000005 INFO  ams/kiosk-007 9ms ok
0000006 WARN  lis/kiosk-118 77ms fan-high
entries: 6
skipped: 2
level FAIL: 1
level INFO: 2
level WARN: 2
level warn: 1
--- empty count=1 type=String ---
shift brief
entries: 0
skipped: 0
'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    [System.IO.File]::WriteAllText((Join-Path $T 'small.log'), $smallLog + "`n")
    [System.IO.File]::WriteAllText((Join-Path $T 'empty.log'), '')

    # Deterministic 500k-line collector log (fixed LCG, no wall clock, no
    # randomness source): ~21 MB rendered brief. Every 997th line is a
    # malformed sensor dump, every 1009th is blank.
    $sites = @('ams', 'fra', 'lis', 'zrh')
    $levels = @('INFO', 'WARN', 'FAIL', 'warn')
    $notes = @('ok', 'fan-high', 'net-drop', 'disk-slow', 'sensor-reset')
    $gen = [System.Collections.Generic.List[string]]::new()
    $state = [long]20260716
    for ($i = 1; $i -le 500000; $i++) {
        if ($i % 1009 -eq 0) { $gen.Add(''); continue }
        if ($i % 997 -eq 0) { $gen.Add("sensor glitch dump $i"); continue }
        $state = ($state * 1103515245 + 12345) % 2147483648
        $v = [int]$state
        $gen.Add(('{0} kiosk-{1} {2} {3} {4}' -f $sites[$v % 4], (($v -shr 2) % 400), $levels[($v -shr 11) % 4], (($v -shr 13) % 997), $notes[($v -shr 5) % 5]))
    }
    [System.IO.File]::WriteAllText((Join-Path $T 'big.log'), ($gen -join "`n") + "`n")
    $gen = $null

    $driverPath = Join-Path $T 'driver.ps1'
    [System.IO.File]::WriteAllText($driverPath, $driver)
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    & pwsh -NoProfile -NonInteractive -File $driverPath 1>$outFile 2>$errFile
    $rc = $LASTEXITCODE
    $out = [System.IO.File]::ReadAllText($outFile)
    $err = [System.IO.File]::ReadAllText($errFile)

    Assert-True 'driver exits 0' ($rc -eq 0)
    Assert-Eq 'driver stderr empty' '' $err

    $expectedOut = $expected + "`n" +
        '--- missing ERR briefkit: log not found: ' + (Join-Path $T 'no-such.log') + " ---`n" +
        "--- big count=1 len=21166347 sha256=d4286ab3b8e1c30e952d9a662bba8a40ddb2221bea9b06a495551f31bd566a51 ---`n"
    Assert-Eq 'behavior block' $expectedOut $out
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
