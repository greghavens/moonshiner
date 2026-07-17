# Acceptance harness for textgrid.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_textgrid.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$lib = Join-Path $PSScriptRoot 'textgrid.ps1'
if (-not (Test-Path -LiteralPath $lib -PathType Leaf)) {
    Write-Output 'FAIL textgrid.ps1 not found in the workspace root'
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

# Rendering cmdlets are banned in output contracts; the renderer is hand-rolled
# and the source is part of the contract.
$src = [System.IO.File]::ReadAllText($lib)
$srcLower = $src.ToLowerInvariant()
foreach ($banned in @('format-table', 'format-list', 'format-wide', 'out-string')) {
    Assert-True "source does not use $banned" (-not $srcLower.Contains($banned))
}
Assert-True 'source does not use the ft alias' (-not [regex]::IsMatch($src, '(?i)(?<![\w-])ft(?![\w-])'))

$driver = @'
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'textgrid.ps1')

function Out-Err {
    param([string]$Label, [scriptblock]$Body)
    try { $null = & $Body; Write-Output "$Label=[NO-ERROR]" }
    catch { Write-Output "$Label=[ERR $($_.Exception.Message)]" }
}

$hosts = @(
    [pscustomobject]@{ Host = 'cache01'; Cpu = [double]3.5; Conns = [int]12 }
    [pscustomobject]@{ Host = 'db-primary'; Cpu = [double]12.0; Conns = [int]4 }
    [pscustomobject]@{ Host = 'api'; Cpu = [double]0.25; Conns = [int]112 }
)
$fleet = @(
    [pscustomobject]@{ Host = 'analytics-warehouse-02'; EnvironmentGroup = 'batch-processing'; Note = $null }
    [pscustomobject]@{ Host = 'api'; EnvironmentGroup = 'edge'; Note = 'drain first' }
)
$builds = @(
    [pscustomobject]@{ Id = [long]123456789012; Build = '1042'; Qty = [int]7 }
    [pscustomobject]@{ Id = [long]33; Build = '9'; Qty = [int]1250 }
)
$single = @([pscustomobject]@{ Note = $null })

Write-Output '<<hosts>>'
Write-Output (Format-TextGrid -Rows $hosts)
Write-Output '<<clipped>>'
Write-Output (Format-TextGrid -Rows $fleet -MaxWidth 12)
Write-Output '<<numeric>>'
Write-Output (Format-TextGrid -Rows $builds -MaxWidth 8)
Write-Output '<<single>>'
Write-Output (Format-TextGrid -Rows $single)
Write-Output "type=[$((Format-TextGrid -Rows $hosts) -is [string])]"
Out-Err 'err.norows' { Format-TextGrid -Rows @() }
Out-Err 'err.maxwidth' { Format-TextGrid -Rows $hosts -MaxWidth 3 }
'@

# Expected grids, assembled with the same width/alignment rules the spec pins:
# autosized column widths, numeric right-align, two-space separator, TrimEnd.
$expHosts = @(
    ('{0,-10}  {1,4}  {2,5}' -f 'Host', 'Cpu', 'Conns')
    ('{0}  {1}  {2}' -f ('-' * 10), ('-' * 4), ('-' * 5))
    ('{0,-10}  {1,4}  {2,5}' -f 'cache01', '3.5', '12')
    ('{0,-10}  {1,4}  {2,5}' -f 'db-primary', '12', '4')
    ('{0,-10}  {1,4}  {2,5}' -f 'api', '0.25', '112')
) | ForEach-Object { $_.TrimEnd() }

$expClipped = @(
    ('{0,-12}  {1,-12}  {2,-11}' -f 'Host', 'Environme...', 'Note')
    ('{0}  {1}  {2}' -f ('-' * 12), ('-' * 12), ('-' * 11))
    ('{0,-12}  {1,-12}  {2,-11}' -f 'analytics...', 'batch-pro...', '')
    ('{0,-12}  {1,-12}  {2,-11}' -f 'api', 'edge', 'drain first')
) | ForEach-Object { $_.TrimEnd() }

$expNumeric = @(
    ('{0,8}  {1,-5}  {2,4}' -f 'Id', 'Build', 'Qty')
    ('{0}  {1}  {2}' -f ('-' * 8), ('-' * 5), ('-' * 4))
    ('{0,8}  {1,-5}  {2,4}' -f '12345...', '1042', '7')
    ('{0,8}  {1,-5}  {2,4}' -f '33', '9', '1250')
) | ForEach-Object { $_.TrimEnd() }

$expSingle = @('Note', '----', '')

$expected = ((@('<<hosts>>') + $expHosts +
    @('<<clipped>>') + $expClipped +
    @('<<numeric>>') + $expNumeric +
    @('<<single>>') + $expSingle +
    @('type=[True]',
      'err.norows=[ERR textgrid: no rows]',
      'err.maxwidth=[ERR textgrid: MaxWidth must be at least 4]')) -join "`n") + "`n"

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
    Assert-Eq 'rendered grids' $expected $out
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
