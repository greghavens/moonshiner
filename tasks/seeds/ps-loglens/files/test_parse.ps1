# Stage 1 of 3: ingest + normalize. Run standalone or via test_loglens.ps1.
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

$quietExpected = @'
{"Timestamp":"2026-07-01T09:00:00Z","Source":"auth","Host":"idp01","Session":"S-2001","User":"tilda","Level":"info","Event":"auth-ok"}
{"Timestamp":"2026-07-01T09:00:05Z","Source":"gateway","Host":"gw01","Session":"S-2001","User":"","Level":"info","Event":"GET /home"}
{"Timestamp":"2026-07-01T09:00:08Z","Source":"app","Host":"app01","Session":"S-2001","User":"tilda","Level":"info","Event":"home-view"}
'@ + "`n"

$busyExpected = @'
{"Timestamp":"2026-06-30T08:00:01Z","Source":"auth","Host":"idp01","Session":"S-1001","User":"mora","Level":"info","Event":"auth-ok"}
{"Timestamp":"2026-06-30T08:00:05Z","Source":"gateway","Host":"gw01","Session":"S-1001","User":"","Level":"info","Event":"GET /login"}
{"Timestamp":"2026-06-30T08:00:10Z","Source":"app","Host":"app01","Session":"S-1001","User":"mora","Level":"info","Event":"login-ok"}
{"Timestamp":"2026-06-30T08:00:20Z","Source":"gateway","Host":"gw01","Session":"S-1001","User":"","Level":"info","Event":"GET /dash"}
{"Timestamp":"2026-06-30T08:00:25Z","Source":"app","Host":"app01","Session":"S-1001","User":"","Level":"info","Event":"dash-view"}
{"Timestamp":"2026-06-30T08:03:00Z","Source":"gateway","Host":"gw01","Session":"S-1006","User":"","Level":"info","Event":"GET /health"}
{"Timestamp":"2026-06-30T08:03:30Z","Source":"gateway","Host":"gw01","Session":"S-1006","User":"","Level":"info","Event":"GET /metrics"}
{"Timestamp":"2026-06-30T08:04:40Z","Source":"auth","Host":"idp01","Session":"S-1002","User":"petra","Level":"warn","Event":"auth-fail"}
{"Timestamp":"2026-06-30T08:04:55Z","Source":"auth","Host":"idp01","Session":"S-1002","User":"petra","Level":"warn","Event":"auth-fail"}
{"Timestamp":"2026-06-30T08:05:00Z","Source":"auth","Host":"idp02","Session":"S-1003","User":"quinn","Level":"info","Event":"auth-ok"}
{"Timestamp":"2026-06-30T08:05:05Z","Source":"auth","Host":"idp01","Session":"S-1002","User":"petra","Level":"warn","Event":"auth-fail"}
{"Timestamp":"2026-06-30T08:05:10Z","Source":"gateway","Host":"gw02","Session":"S-1003","User":"","Level":"info","Event":"GET /files"}
{"Timestamp":"2026-06-30T08:05:20Z","Source":"app","Host":"app01","Session":"S-1003","User":"quinn","Level":"info","Event":"file-list"}
{"Timestamp":"2026-06-30T08:05:30Z","Source":"auth","Host":"idp01","Session":"S-1002","User":"petra","Level":"info","Event":"auth-ok"}
{"Timestamp":"2026-06-30T08:05:40Z","Source":"app","Host":"app02","Session":"S-1003","User":"quinn","Level":"info","Event":"file-move"}
{"Timestamp":"2026-06-30T08:07:45Z","Source":"gateway","Host":"gw02","Session":"S-1004","User":"","Level":"error","Event":"POST /jobs"}
{"Timestamp":"2026-06-30T08:08:00Z","Source":"app","Host":"app02","Session":"S-1004","User":"","Level":"error","Event":"job-retry"}
{"Timestamp":"2026-06-30T08:08:20Z","Source":"app","Host":"app03","Session":"S-1004","User":"rado","Level":"error","Event":"job-crash"}
{"Timestamp":"2026-06-30T08:08:30Z","Source":"auth","Host":"idp02","Session":"S-1005","User":"sena","Level":"warn","Event":"auth-fail"}
{"Timestamp":"2026-06-30T08:08:40Z","Source":"auth","Host":"idp02","Session":"S-1005","User":"sena","Level":"warn","Event":"auth-fail"}
{"Timestamp":"2026-06-30T08:08:55Z","Source":"auth","Host":"idp02","Session":"S-1005","User":"sena","Level":"warn","Event":"auth-fail"}
{"Timestamp":"2026-06-30T08:08:58Z","Source":"auth","Host":"idp02","Session":"S-1005","User":"sena","Level":"warn","Event":"auth-fail"}
{"Timestamp":"2026-06-30T08:09:00Z","Source":"gateway","Host":"gw02","Session":"S-1005","User":"","Level":"warn","Event":"GET /admin"}
{"Timestamp":"2026-06-30T08:09:30Z","Source":"gateway","Host":"gw03","Session":"S-1005","User":"","Level":"info","Event":"GET /admin"}
{"Timestamp":"2026-06-30T08:10:00Z","Source":"gateway","Host":"gw03","Session":"S-1005","User":"","Level":"error","Event":"POST /export"}
{"Timestamp":"2026-06-30T08:10:20Z","Source":"app","Host":"app03","Session":"S-1005","User":"sena","Level":"error","Event":"export-fail"}
{"Timestamp":"2026-06-30T08:10:40Z","Source":"app","Host":"app03","Session":"S-1005","User":"","Level":"error","Event":"export-fail"}
{"Timestamp":"2026-06-30T08:11:00Z","Source":"gateway","Host":"gw04","Session":"S-1004","User":"","Level":"info","Event":"GET /jobs/9"}
{"Timestamp":"2026-06-30T08:12:00Z","Source":"gateway","Host":"gw04","Session":"S-1004","User":"","Level":"info","Event":"GET /jobs/9/log"}
{"Timestamp":"2026-06-30T08:12:30Z","Source":"app","Host":"app04","Session":"S-1006","User":"","Level":"warn","Event":"cache-stale"}
'@ + "`n"

$malformedExpected = @'
loglens: malformed line app04.log:1
loglens: malformed line auth02.csv:5
loglens: malformed line gw04.log:2
'@ + "`n"

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- quiet corpus: three sources, one session, byte-exact NDJSON ---
    Invoke-Tool @('normalize', '-LogDir', $quiet)
    Assert-True 'normalize quiet: exit 0' ($RC -eq 0)
    Assert-Eq 'normalize quiet: stderr empty' '' $ERR
    Assert-Eq 'normalize quiet: NDJSON stream' $quietExpected $OUT

    # --- busy corpus: full merge across sources, malformed lines reported ---
    Invoke-Tool @('normalize', '-LogDir', $logs)
    Assert-True 'normalize busy: exit 0' ($RC -eq 0)
    Assert-Eq 'normalize busy: malformed-line diagnostics on stderr' $malformedExpected $ERR
    Assert-Eq 'normalize busy: NDJSON stream' $busyExpected $OUT

    # --- CLI contract ---
    Invoke-Tool @('frobnicate', '-LogDir', $logs)
    Assert-True 'unknown command: exit 64' ($RC -eq 64)
    Assert-Eq 'unknown command: stdout empty' '' $OUT
    Assert-Eq 'unknown command: usage line' "usage: loglens.ps1 normalize|sessions|escalations -LogDir <dir>`n" $ERR

    Invoke-Tool @('normalize', '-LogDir', 'no_such_logs')
    Assert-True 'missing dir: exit 66' ($RC -eq 66)
    Assert-Eq 'missing dir: stdout empty' '' $OUT
    Assert-Eq 'missing dir: message' "loglens: log directory not found: no_such_logs`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
