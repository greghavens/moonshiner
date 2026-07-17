# Acceptance harness for loglens: runs the three stage suites in order
# (parse -> correlate -> report) and aggregates their check counts.
# Run from the workspace root:  pwsh -NoProfile -File test_loglens.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$tool = Join-Path $PSScriptRoot 'loglens.ps1'
if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
    Write-Output 'FAIL loglens.ps1 not found in the workspace root'
    exit 1
}

$script:checks = 0
$script:fails = 0

foreach ($stage in 'test_parse.ps1', 'test_correlate.ps1', 'test_report.ps1') {
    $path = Join-Path $PSScriptRoot $stage
    $output = & pwsh -NoProfile -NonInteractive -File $path 2>&1 | Out-String
    $rc = $LASTEXITCODE
    if ($rc -eq 0 -and $output -cmatch 'all checks passed \((\d+) checks\)') {
        $script:checks += [int]$Matches[1]
        Write-Output "stage $stage passed ($($Matches[1]) checks)"
    } else {
        $script:fails++
        $script:checks++
        Write-Output "FAIL stage $stage (exit $rc)"
        Write-Output $output
    }
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
