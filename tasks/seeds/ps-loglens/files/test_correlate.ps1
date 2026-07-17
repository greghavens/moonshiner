# Stage 2 of 3: session correlation. Run standalone or via test_loglens.ps1.
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

$busySessions = @'
{"Session":"S-1001","User":"mora","Start":"2026-06-30T08:00:01Z","End":"2026-06-30T08:00:25Z","DurationSeconds":24,"Hosts":"app01,gw01,idp01","Sources":"app,auth,gateway","Events":5,"Errors":0,"AuthFailures":0}
{"Session":"S-1002","User":"petra","Start":"2026-06-30T08:04:40Z","End":"2026-06-30T08:05:30Z","DurationSeconds":50,"Hosts":"idp01","Sources":"auth","Events":4,"Errors":0,"AuthFailures":3}
{"Session":"S-1003","User":"quinn","Start":"2026-06-30T08:05:00Z","End":"2026-06-30T08:05:40Z","DurationSeconds":40,"Hosts":"app01,app02,gw02,idp02","Sources":"app,auth,gateway","Events":4,"Errors":0,"AuthFailures":0}
{"Session":"S-1004","User":"rado","Start":"2026-06-30T08:07:45Z","End":"2026-06-30T08:12:00Z","DurationSeconds":255,"Hosts":"app02,app03,gw02,gw04","Sources":"app,gateway","Events":5,"Errors":3,"AuthFailures":0}
{"Session":"S-1005","User":"sena","Start":"2026-06-30T08:08:30Z","End":"2026-06-30T08:10:40Z","DurationSeconds":130,"Hosts":"app03,gw02,gw03,idp02","Sources":"app,auth,gateway","Events":9,"Errors":3,"AuthFailures":4}
{"Session":"S-1006","User":"","Start":"2026-06-30T08:03:00Z","End":"2026-06-30T08:12:30Z","DurationSeconds":570,"Hosts":"app04,gw01","Sources":"app,gateway","Events":3,"Errors":0,"AuthFailures":0}
'@ + "`n"

$quietSessions = @'
{"Session":"S-2001","User":"tilda","Start":"2026-07-01T09:00:00Z","End":"2026-07-01T09:00:08Z","DurationSeconds":8,"Hosts":"app01,gw01,idp01","Sources":"app,auth,gateway","Events":3,"Errors":0,"AuthFailures":0}
'@ + "`n"

$malformedExpected = @'
loglens: malformed line app04.log:1
loglens: malformed line auth02.csv:5
loglens: malformed line gw04.log:2
'@ + "`n"

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    Invoke-Tool @('sessions', '-LogDir', $logs)
    Assert-True 'sessions busy: exit 0' ($RC -eq 0)
    Assert-Eq 'sessions busy: malformed-line diagnostics on stderr' $malformedExpected $ERR
    Assert-Eq 'sessions busy: one NDJSON object per session' $busySessions $OUT

    Invoke-Tool @('sessions', '-LogDir', $quiet)
    Assert-True 'sessions quiet: exit 0' ($RC -eq 0)
    Assert-Eq 'sessions quiet: stderr empty' '' $ERR
    Assert-Eq 'sessions quiet: single session' $quietSessions $OUT
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
