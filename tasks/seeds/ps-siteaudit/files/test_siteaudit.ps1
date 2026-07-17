# Acceptance harness for siteaudit.ps1 and its rules/severity support.
# Run from the workspace root:  pwsh -NoProfile -File test_siteaudit.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$tool = Join-Path $PSScriptRoot 'siteaudit.ps1'
if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
    Write-Output 'FAIL siteaudit.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File $tool @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function Write-Fixture {
    param([string]$Name, [string]$Content)
    $p = Join-Path $T $Name
    [System.IO.File]::WriteAllText($p, $Content)
    return $p
}

function New-SiteFile {
    param([string]$Rel, [int]$Bytes, [string]$MtimeUtc)
    $p = Join-Path (Join-Path $T 'site') $Rel
    $dir = Split-Path -Parent $p
    New-Item -ItemType Directory -Force -Path $dir > $null
    [System.IO.File]::WriteAllText($p, ('x' * $Bytes))
    $stamp = [datetime]::Parse($MtimeUtc, [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::AdjustToUniversal)
    [System.IO.File]::SetLastWriteTimeUtc($p, $stamp)
}

$NOW = '2026-07-01T00:00:00Z'

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    $site = Join-Path $T 'site'
    New-SiteFile 'index.html' 4096 '2026-06-20T00:00:00Z'
    New-SiteFile 'assets/big.png' 5000 '2026-06-20T00:00:00Z'
    New-SiteFile 'assets/Logo Old.png' 300 '2026-06-20T00:00:00Z'
    New-SiteFile 'archive/report-2019.html' 100 '2019-05-01T00:00:00Z'
    New-SiteFile 'archive/data 2018.csv' 80 '2019-05-01T00:00:00Z'
    New-SiteFile 'legacy/huge.dat' 8000 '2019-05-01T00:00:00Z'
    New-SiteFile 'archive/edge.log' 50 '2026-04-02T00:00:00Z'

    # ---------------------------------------------------------------
    # Existing behavior, frozen: the classic report format.
    # ---------------------------------------------------------------

    Invoke-Tool @('-Root', $site, '-Now', $NOW)
    Assert-True 'base: exit 65' ($RC -eq 65)
    Assert-Eq 'base: stderr empty' '' $ERR
    Assert-Eq 'base: findings' @'
badname archive/data 2018.csv
badname assets/Logo Old.png
oversize assets/big.png
oversize legacy/huge.dat
stale archive/data 2018.csv
stale archive/report-2019.html
stale legacy/huge.dat

'@ $OUT

    Invoke-Tool @('-Root', $site, '-Now', $NOW, '-MaxBytes', '6000', '-MaxAgeDays', '3000')
    Assert-True 'thresholds: exit 65' ($RC -eq 65)
    Assert-Eq 'thresholds: findings' @'
badname archive/data 2018.csv
badname assets/Logo Old.png
oversize legacy/huge.dat

'@ $OUT

    $clean = Join-Path $T 'cleanroot'
    New-Item -ItemType Directory -Force -Path $clean > $null
    [System.IO.File]::WriteAllText((Join-Path $clean 'readme.txt'), 'tidy')
    [System.IO.File]::SetLastWriteTimeUtc((Join-Path $clean 'readme.txt'),
        [datetime]::Parse('2026-06-20T00:00:00Z', [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::AdjustToUniversal))
    Invoke-Tool @('-Root', $clean, '-Now', $NOW)
    Assert-True 'clean: exit 0' ($RC -eq 0)
    Assert-Eq 'clean: no output' '' $OUT

    Invoke-Tool @('-Root', $site, '-Now', 'yesterday-ish')
    Assert-True 'bad now: exit 64' ($RC -eq 64)
    Assert-Eq 'bad now: stdout empty' '' $OUT
    Assert-Eq 'bad now: message' "siteaudit: bad -Now value: yesterday-ish`n" $ERR

    $noRoot = Join-Path $T 'gone'
    Invoke-Tool @('-Root', $noRoot, '-Now', $NOW)
    Assert-True 'no root: exit 66' ($RC -eq 66)
    Assert-Eq 'no root: message' "siteaudit: root not found: $noRoot`n" $ERR

    # ---------------------------------------------------------------
    # The feature: a rules file with exclusions and severity overrides,
    # plus -MinSeverity filtering. Report lines gain the level column.
    # ---------------------------------------------------------------

    $rulesMain = Write-Fixture 'rules_main.txt' @'
# freeze-window exceptions
exclude archive/*
severity warn assets/big.png
severity info legacy/*
severity error assets/Logo*
severity info assets/Logo Old.png
'@

    Invoke-Tool @('-Root', $site, '-Now', $NOW, '-Rules', $rulesMain)
    Assert-True 'rules: exit 65' ($RC -eq 65)
    Assert-Eq 'rules: stderr empty' '' $ERR
    Assert-Eq 'rules: findings' @'
info badname assets/Logo Old.png
info oversize legacy/huge.dat
info stale legacy/huge.dat
warn oversize assets/big.png

'@ $OUT

    Invoke-Tool @('-Root', $site, '-Now', $NOW, '-Rules', $rulesMain, '-MinSeverity', 'warn')
    Assert-True 'rules+warn: exit 65' ($RC -eq 65)
    Assert-Eq 'rules+warn: findings' @'
warn oversize assets/big.png

'@ $OUT

    Invoke-Tool @('-Root', $site, '-Now', $NOW, '-Rules', $rulesMain, '-MinSeverity', 'error')
    Assert-True 'rules+error: exit 0' ($RC -eq 0)
    Assert-Eq 'rules+error: no output' '' $OUT

    Invoke-Tool @('-Root', $site, '-Now', $NOW, '-MinSeverity', 'warn')
    Assert-True 'minsev alone: exit 65' ($RC -eq 65)
    Assert-Eq 'minsev alone: findings' @'
error oversize assets/big.png
error oversize legacy/huge.dat
warn stale archive/data 2018.csv
warn stale archive/report-2019.html
warn stale legacy/huge.dat

'@ $OUT

    # patterns match case-sensitively: none of these hit anything
    $rulesCase = Write-Fixture 'rules_case.txt' @'
exclude Archive/*
severity error *.CSV
'@
    Invoke-Tool @('-Root', $site, '-Now', $NOW, '-Rules', $rulesCase)
    Assert-True 'rules case: exit 65' ($RC -eq 65)
    Assert-Eq 'rules case: findings' @'
error oversize assets/big.png
error oversize legacy/huge.dat
info badname archive/data 2018.csv
info badname assets/Logo Old.png
warn stale archive/data 2018.csv
warn stale archive/report-2019.html
warn stale legacy/huge.dat

'@ $OUT

    $rulesBad1 = Write-Fixture 'rules_bad1.txt' @'
exclude archive/*
serverity warn *.png
'@
    Invoke-Tool @('-Root', $site, '-Now', $NOW, '-Rules', $rulesBad1)
    Assert-True 'bad verb: exit 64' ($RC -eq 64)
    Assert-Eq 'bad verb: stdout empty' '' $OUT
    Assert-Eq 'bad verb: message' "siteaudit: rules_bad1.txt: bad rule line 2`n" $ERR

    $rulesBad2 = Write-Fixture 'rules_bad2.txt' @'
severity urgent assets/*
'@
    Invoke-Tool @('-Root', $site, '-Now', $NOW, '-Rules', $rulesBad2)
    Assert-True 'bad level: exit 64' ($RC -eq 64)
    Assert-Eq 'bad level: message' "siteaudit: rules_bad2.txt: bad rule line 1`n" $ERR

    $noRules = Join-Path $T 'norules.txt'
    Invoke-Tool @('-Root', $site, '-Now', $NOW, '-Rules', $noRules)
    Assert-True 'missing rules: exit 66' ($RC -eq 66)
    Assert-Eq 'missing rules: message' "siteaudit: rules file not found: $noRules`n" $ERR

    Invoke-Tool @('-Root', $site, '-Now', $NOW, '-MinSeverity', 'urgent')
    Assert-True 'bad minsev: exit 64' ($RC -eq 64)
    Assert-Eq 'bad minsev: message' "siteaudit: bad -MinSeverity value: urgent`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
