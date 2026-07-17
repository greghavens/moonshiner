# Acceptance harness for logsift.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_logsift.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'logsift.ps1') -PathType Leaf)) {
    Write-Output 'FAIL logsift.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'logsift.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

$mainLog = (@(
    '2026-07-10 08:15:02 INFO scheduler starting run 42'
    '2026-07-10 08:15:03 DEBUG dbpool warm connections: 8'
    '2026-07-10 08:15:04 ERROR api request failed for /v1/plots'
    '    retry 1 of 3 scheduled'
    ("`t" + 'upstream said: gateway busy')
    '2026-07-10 08:15:09 WARN scheduler run 42 slow'
    ''
    '2026-07-10 08:15:10 info scheduler lowercase probe line'
    '2026-07-10 08:15:11 INFO  api double space probe'
    'stray text without a stamp'
    '2026-07-10 08:15:12 INFO api recovered'
) -join "`n") + "`n"

$coldLog = "   indented orphan`n2026-07-10 09:00:00 INFO boot ok`n"

$expectedSkip = @'
{
  "total": 5,
  "malformed": 3,
  "levels": {
    "DEBUG": 1,
    "ERROR": 1,
    "INFO": 2,
    "WARN": 1
  },
  "records": [
    {
      "timestamp": "2026-07-10 08:15:02",
      "level": "INFO",
      "component": "scheduler",
      "message": "starting run 42"
    },
    {
      "timestamp": "2026-07-10 08:15:03",
      "level": "DEBUG",
      "component": "dbpool",
      "message": "warm connections: 8"
    },
    {
      "timestamp": "2026-07-10 08:15:04",
      "level": "ERROR",
      "component": "api",
      "message": "request failed for /v1/plots\nretry 1 of 3 scheduled\nupstream said: gateway busy"
    },
    {
      "timestamp": "2026-07-10 08:15:09",
      "level": "WARN",
      "component": "scheduler",
      "message": "run 42 slow"
    },
    {
      "timestamp": "2026-07-10 08:15:12",
      "level": "INFO",
      "component": "api",
      "message": "recovered"
    }
  ]
}
'@

$expectedKeep = @'
{
  "total": 8,
  "malformed": 3,
  "levels": {
    "DEBUG": 1,
    "ERROR": 1,
    "INFO": 2,
    "RAW": 3,
    "WARN": 1
  },
  "records": [
    {
      "timestamp": "2026-07-10 08:15:02",
      "level": "INFO",
      "component": "scheduler",
      "message": "starting run 42"
    },
    {
      "timestamp": "2026-07-10 08:15:03",
      "level": "DEBUG",
      "component": "dbpool",
      "message": "warm connections: 8"
    },
    {
      "timestamp": "2026-07-10 08:15:04",
      "level": "ERROR",
      "component": "api",
      "message": "request failed for /v1/plots\nretry 1 of 3 scheduled\nupstream said: gateway busy"
    },
    {
      "timestamp": "2026-07-10 08:15:09",
      "level": "WARN",
      "component": "scheduler",
      "message": "run 42 slow"
    },
    {
      "timestamp": "",
      "level": "RAW",
      "component": "-",
      "message": "2026-07-10 08:15:10 info scheduler lowercase probe line"
    },
    {
      "timestamp": "",
      "level": "RAW",
      "component": "-",
      "message": "2026-07-10 08:15:11 INFO  api double space probe"
    },
    {
      "timestamp": "",
      "level": "RAW",
      "component": "-",
      "message": "stray text without a stamp"
    },
    {
      "timestamp": "2026-07-10 08:15:12",
      "level": "INFO",
      "component": "api",
      "message": "recovered"
    }
  ]
}
'@

$expectedCold = @'
{
  "total": 1,
  "malformed": 1,
  "levels": {
    "INFO": 1
  },
  "records": [
    {
      "timestamp": "2026-07-10 09:00:00",
      "level": "INFO",
      "component": "boot",
      "message": "ok"
    }
  ]
}
'@

$expectedEmpty = @'
{
  "total": 0,
  "malformed": 0,
  "levels": {},
  "records": []
}
'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null
    $mainPath = Join-Path $T 'app.log'
    $coldPath = Join-Path $T 'cold.log'
    $emptyPath = Join-Path $T 'empty.log'
    [System.IO.File]::WriteAllText($mainPath, $mainLog)
    [System.IO.File]::WriteAllText($coldPath, $coldLog)
    [System.IO.File]::WriteAllText($emptyPath, '')

    # --- default policy is skip ---
    Invoke-Tool @('-Path', $mainPath)
    Assert-True 'default: exit 0' ($RC -eq 0)
    Assert-Eq 'default: stderr empty' '' $ERR
    Assert-Eq 'default: report' ($expectedSkip + "`n") $OUT

    # --- explicit -OnMalformed skip matches the default ---
    Invoke-Tool @('-Path', $mainPath, '-OnMalformed', 'skip')
    Assert-True 'skip: exit 0' ($RC -eq 0)
    Assert-Eq 'skip: report' ($expectedSkip + "`n") $OUT

    # --- keep policy records the unparsed lines in place ---
    Invoke-Tool @('-Path', $mainPath, '-OnMalformed', 'keep')
    Assert-True 'keep: exit 0' ($RC -eq 0)
    Assert-Eq 'keep: stderr empty' '' $ERR
    Assert-Eq 'keep: report' ($expectedKeep + "`n") $OUT

    # --- error policy stops on the first unparsed line ---
    Invoke-Tool @('-Path', $mainPath, '-OnMalformed', 'error')
    Assert-True 'error: exit 65' ($RC -eq 65)
    Assert-Eq 'error: stdout empty' '' $OUT
    Assert-Eq 'error: message' "logsift: malformed line 8: 2026-07-10 08:15:10 info scheduler lowercase probe line`n" $ERR

    # --- a continuation with no record to attach to is malformed ---
    Invoke-Tool @('-Path', $coldPath)
    Assert-True 'orphan skip: exit 0' ($RC -eq 0)
    Assert-Eq 'orphan skip: report' ($expectedCold + "`n") $OUT

    Invoke-Tool @('-Path', $coldPath, '-OnMalformed', 'error')
    Assert-True 'orphan error: exit 65' ($RC -eq 65)
    Assert-Eq 'orphan error: message' "logsift: malformed line 1:    indented orphan`n" $ERR

    # --- empty log file: empty but well-formed report ---
    Invoke-Tool @('-Path', $emptyPath)
    Assert-True 'empty: exit 0' ($RC -eq 0)
    Assert-Eq 'empty: report' ($expectedEmpty + "`n") $OUT

    # --- input problems ---
    $gone = Join-Path $T 'gone.log'
    Invoke-Tool @('-Path', $gone)
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "logsift: log not found: $gone`n" $ERR

    Invoke-Tool @('-Path', '')
    Assert-True 'empty path: exit 64' ($RC -eq 64)
    Assert-Eq 'empty path: message' "logsift: -Path requires a non-empty path`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
