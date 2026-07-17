# Stage 3 of 3: escalation report. Run standalone or via test_loglens.ps1.
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

$T = Join-Path $PSScriptRoot '_t'
$logs = Join-Path $PSScriptRoot 'fixtures' 'logs'
$quiet = Join-Path $PSScriptRoot 'fixtures' 'quiet'
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

$busyEscalations = @'
high S-1002 repeated auth failures (3)
high S-1005 repeated auth failures (4)
medium S-1003 session roamed 4 hosts
medium S-1004 session roamed 4 hosts
medium S-1005 session roamed 4 hosts
low S-1004 elevated error volume (3)
low S-1005 elevated error volume (3)
'@ + "`n"

$malformedExpected = @'
loglens: malformed line app04.log:1
loglens: malformed line auth02.csv:5
loglens: malformed line gw04.log:2
'@ + "`n"

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- busy corpus: every rule fires, severity groups in order ---
    Invoke-Tool @('escalations', '-LogDir', $logs)
    Assert-True 'escalations busy: exit 65 when anything escalates' ($RC -eq 65)
    Assert-Eq 'escalations busy: malformed-line diagnostics on stderr' $malformedExpected $ERR
    Assert-Eq 'escalations busy: report lines' $busyEscalations $OUT

    # --- quiet corpus: nothing to report ---
    Invoke-Tool @('escalations', '-LogDir', $quiet)
    Assert-True 'escalations quiet: exit 0' ($RC -eq 0)
    Assert-Eq 'escalations quiet: stderr empty' '' $ERR
    Assert-Eq 'escalations quiet: no output' '' $OUT
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
