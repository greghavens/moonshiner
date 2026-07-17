# Acceptance harness for binseek.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_binseek.ps1
#
# OP-COUNT GATE (this is the perf contract): every record's Label property in
# this rig is a ScriptProperty that bumps a shared counter when read — the
# same shape as the production objects, where Label proxies a depot backend
# call. The counter assertions are EXACT: resolving M queries over N records
# must read Label exactly N times, total. The retired implementation ran a
# Where-Object over all records for every query, which reads N labels per
# query (case B below would show 2000 x 4000 = 8,000,000 reads instead of
# 2000). There are deliberately no wall-clock assertions in this file.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$lib = Join-Path $PSScriptRoot 'binseek.ps1'
if (-not (Test-Path -LiteralPath $lib -PathType Leaf)) {
    Write-Output 'FAIL binseek.ps1 not found in the workspace root'
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

# The instrumented records only exist in-process, so all behavior checks run
# inside ONE child pwsh that dot-sources the library, builds the rigged
# records, and prints a labeled block; the block is compared byte-exact.
$driver = @'
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'binseek.ps1')

function New-Rec {
    param([string]$Label, [string]$Bin, [string]$Owner, [ref]$Counter)
    $r = [pscustomobject]@{ Bin = $Bin; Owner = $Owner }
    $r | Add-Member -MemberType ScriptProperty -Name Label -Value { $Counter.Value++; $Label }.GetNewClosure()
    return $r
}

# --- case A: semantics (dup labels, case-sensitive matching, misses, repeats)
$cA = [ref]0
$specA = @(
    @('tote-12', 'A-03', 'kim'), @('DOCK-9', 'D-01', 'rosa'), @('dock-9', 'D-14', 'petr'),
    @('tote-12', 'A-99', 'olga'), @('rack-3', 'R-08', 'kim'), @('pallet-88', 'P-02', 'dana'))
$recsA = @($specA | ForEach-Object { New-Rec -Label $_[0] -Bin $_[1] -Owner $_[2] -Counter $cA })
$qA = @('dock-9', 'tote-12', 'DOCK-9', 'crate-5', 'dock-9', 'TOTE-12')
$rA = @(Resolve-BinQueries -Records $recsA -Queries $qA)
foreach ($line in $rA) { Write-Output ([string]$line) }
Write-Output "countA=$($rA.Count) probesA=$($cA.Value)"

# --- case B: volume; exact read count is the gate
$cB = [ref]0
$recsB = [System.Collections.Generic.List[object]]::new()
for ($i = 0; $i -lt 2000; $i++) {
    $recsB.Add((New-Rec -Label ('slot-{0:D4}' -f $i) -Bin ('B-{0:D2}' -f ($i % 40)) -Owner ('crew-' + ($i % 7)) -Counter $cB))
}
$qB = [System.Collections.Generic.List[string]]::new()
for ($i = 0; $i -lt 4000; $i++) { $qB.Add(('slot-{0:D4}' -f (($i * 7) % 2500))) }
$rB = @(Resolve-BinQueries -Records ([object[]]$recsB) -Queries ([string[]]$qB))
$joined = ($rB -join "`n") + "`n"
$sha = [System.Security.Cryptography.SHA256]::Create()
$hash = [System.BitConverter]::ToString($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($joined))).Replace('-', '').ToLowerInvariant()
Write-Output "countB=$($rB.Count) probesB=$($cB.Value) shaB=$hash"
Write-Output "firstB=$($rB[0]) lastB=$($rB[3999])"
'@

$expected = @'
dock-9 -> D-14 owner=petr
tote-12 -> A-03 owner=kim
DOCK-9 -> D-01 owner=rosa
crate-5 -> (unlisted)
dock-9 -> D-14 owner=petr
TOTE-12 -> (unlisted)
countA=6 probesA=6
countB=4000 probesB=2000 shaB=dd9775d5dacc31bc911476df99cdd55495e59db005f987c151af0d8f0ba70932
firstB=slot-0000 -> B-00 owner=crew-0 lastB=slot-0493 -> B-13 owner=crew-3

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null
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
    Assert-Eq 'behavior block' $expected $out
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
