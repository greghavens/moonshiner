# Acceptance harness for announce.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_announce.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'announce.ps1') -PathType Leaf)) {
    Write-Output 'FAIL announce.ps1 not found in the workspace root'
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

function Assert-Contains {
    param([string]$Label, [string]$Needle, [string]$Haystack)
    $script:checks++
    if ($Haystack.Contains($Needle, [System.StringComparison]::Ordinal)) { return }
    $script:fails++
    Write-Output "FAIL $Label"
    Write-Output '--- wanted substring ---'
    Write-Output $Needle
    Write-Output '--- actual ---'
    Write-Output $Haystack
    Write-Output '----------------'
}

$script:RC = 0
$script:OUT = ''
$script:ERR = ''
function Invoke-Tool {
    param([string[]]$CaseArgs = @())
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'announce.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

# The usage block, byte-exact (stdout on -Help; stderr after every 64 error).
$usage = @'
usage: announce.ps1 -Service <name> -Date <yyyy-MM-dd> [options]

options:
  -Service <name>     lowercase service id (letters, digits, dashes)
  -Date <yyyy-MM-dd>  date of the window
  -Start <HH:mm>      24-hour start time (default 22:00)
  -Minutes <n>        window length in minutes, 1..480 (default 30)
  -Impact <level>     one of: low, normal, urgent (default normal)
  -NotesFile <path>   append each non-empty line of this file to the card
  -Help               print this help and exit 0

exit codes: 0 ok, 64 usage error, 65 data error (binding errors exit 1)
'@ + "`n"

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- happy path: defaults ---
    Invoke-Tool @('-Service', 'cache-tier', '-Date', '2026-08-02')
    Assert-True 'defaults: exit 0' ($RC -eq 0)
    Assert-Eq 'defaults: card' "service: cache-tier`nwindow: 2026-08-02 22:00-22:30`nimpact: normal`nminutes: 30`n" $OUT
    Assert-Eq 'defaults: stderr empty' '' $ERR

    # --- happy path: everything explicit ---
    Invoke-Tool @('-Service', 'api-gw', '-Date', '2026-08-15', '-Start', '07:45', '-Minutes', '90', '-Impact', 'low')
    Assert-True 'explicit: exit 0' ($RC -eq 0)
    Assert-Eq 'explicit: card' "service: api-gw`nwindow: 2026-08-15 07:45-09:15`nimpact: low`nminutes: 90`n" $OUT

    # --- window crossing midnight gets the +1d marker ---
    Invoke-Tool @('-Service', 'db-main', '-Date', '2026-08-02', '-Start', '23:50', '-Minutes', '45')
    Assert-True 'wrap: exit 0' ($RC -eq 0)
    Assert-Eq 'wrap: card' "service: db-main`nwindow: 2026-08-02 23:50-00:35+1d`nimpact: normal`nminutes: 45`n" $OUT

    # --- window ending exactly at midnight is next-day 00:00 ---
    Invoke-Tool @('-Service', 'db-main', '-Date', '2026-08-02', '-Start', '23:30', '-Minutes', '30')
    Assert-Eq 'midnight end: card' "service: db-main`nwindow: 2026-08-02 23:30-00:00+1d`nimpact: normal`nminutes: 30`n" $OUT

    # --- longest allowed window from midnight stays same-day ---
    Invoke-Tool @('-Service', 'batch-etl', '-Date', '2026-09-01', '-Start', '00:00', '-Minutes', '480', '-Impact', 'urgent')
    Assert-True 'max window: exit 0' ($RC -eq 0)
    Assert-Eq 'max window: card' "service: batch-etl`nwindow: 2026-09-01 00:00-08:00`nimpact: urgent`nminutes: 480`n" $OUT

    # --- -Help works on its own despite the mandatory parameters ---
    Invoke-Tool @('-Help')
    Assert-True 'help: exit 0' ($RC -eq 0)
    Assert-Eq 'help: usage on stdout' $usage $OUT
    Assert-Eq 'help: stderr empty' '' $ERR

    # --- missing mandatory parameter must ERROR, never prompt ---
    Invoke-Tool @('-Service', 'cache-tier')
    Assert-True 'mandatory miss: exit 1' ($RC -eq 1)
    Assert-Eq 'mandatory miss: stdout empty' '' $OUT
    Assert-Contains 'mandatory miss: engine error names the parameter' 'missing mandatory parameters' $ERR
    Assert-Contains 'mandatory miss: Date is the one missing' 'Date' $ERR

    Invoke-Tool @()
    Assert-True 'no args: exit 1' ($RC -eq 1)
    Assert-Eq 'no args: stdout empty' '' $OUT
    Assert-Contains 'no args: engine error' 'missing mandatory parameters' $ERR

    # --- ValidateSet rejects a bad impact level at binding time ---
    Invoke-Tool @('-Service', 'api-gw', '-Date', '2026-08-15', '-Impact', 'severe')
    Assert-True 'bad impact: exit 1' ($RC -eq 1)
    Assert-Eq 'bad impact: stdout empty' '' $OUT
    Assert-Contains 'bad impact: set named' 'does not belong to the set' $ERR
    Assert-Contains 'bad impact: parameter named' "'Impact'" $ERR

    # --- ValidatePattern rejects a bad service id at binding time ---
    Invoke-Tool @('-Service', 'Cache_Tier', '-Date', '2026-08-15')
    Assert-True 'bad service: exit 1' ($RC -eq 1)
    Assert-Eq 'bad service: stdout empty' '' $OUT
    Assert-Contains 'bad service: pattern failure' 'does not match' $ERR
    Assert-Contains 'bad service: parameter named' "'Service'" $ERR

    # --- ValidatePattern rejects a bad date shape at binding time ---
    Invoke-Tool @('-Service', 'api-gw', '-Date', '8/15/2026')
    Assert-True 'bad date: exit 1' ($RC -eq 1)
    Assert-Contains 'bad date: parameter named' "'Date'" $ERR

    # --- typed binding rejects a non-numeric minutes value ---
    Invoke-Tool @('-Service', 'api-gw', '-Date', '2026-08-15', '-Minutes', 'forty')
    Assert-True 'typed minutes: exit 1' ($RC -eq 1)
    Assert-Contains 'typed minutes: transformation error' "argument transformation on parameter 'Minutes'" $ERR

    # --- a stray positional argument is refused at binding time ---
    Invoke-Tool @('-Service', 'api-gw', '-Date', '2026-08-15', 'tonight')
    Assert-True 'stray positional: exit 1' ($RC -eq 1)
    Assert-Contains 'stray positional: engine error' 'positional parameter cannot be found' $ERR

    # --- start time is checked by the tool itself: 64 + usage on stderr ---
    Invoke-Tool @('-Service', 'api-gw', '-Date', '2026-08-15', '-Start', '24:00')
    Assert-True 'start 24:00: exit 64' ($RC -eq 64)
    Assert-Eq 'start 24:00: stdout empty' '' $OUT
    Assert-Eq 'start 24:00: message + usage' ("announce: invalid start time: 24:00`n" + $usage) $ERR

    Invoke-Tool @('-Service', 'api-gw', '-Date', '2026-08-15', '-Start', '9:30')
    Assert-True 'start 9:30: exit 64' ($RC -eq 64)
    Assert-Eq 'start 9:30: message + usage' ("announce: invalid start time: 9:30`n" + $usage) $ERR

    # --- minutes range is checked by the tool itself: 64 + usage ---
    Invoke-Tool @('-Service', 'api-gw', '-Date', '2026-08-15', '-Minutes', '0')
    Assert-True 'minutes 0: exit 64' ($RC -eq 64)
    Assert-Eq 'minutes 0: message + usage' ("announce: minutes out of range: 0`n" + $usage) $ERR

    Invoke-Tool @('-Service', 'api-gw', '-Date', '2026-08-15', '-Minutes', '481')
    Assert-True 'minutes 481: exit 64' ($RC -eq 64)
    Assert-Eq 'minutes 481: message + usage' ("announce: minutes out of range: 481`n" + $usage) $ERR

    # --- notes file lines are appended, blanks dropped, tails trimmed ---
    $notes = Join-Path $T 'notes.txt'
    [System.IO.File]::WriteAllText($notes, "primary db failover drill`n`nexpect brief 5xx from the edge   `n")
    Invoke-Tool @('-Service', 'db-main', '-Date', '2026-08-02', '-NotesFile', $notes)
    Assert-True 'notes: exit 0' ($RC -eq 0)
    Assert-Eq 'notes: card' "service: db-main`nwindow: 2026-08-02 22:00-22:30`nimpact: normal`nminutes: 30`nnote: primary db failover drill`nnote: expect brief 5xx from the edge`n" $OUT

    # --- notes file that does not exist is a data error: 65, no usage ---
    $missing = Join-Path $T 'nope.txt'
    Invoke-Tool @('-Service', 'db-main', '-Date', '2026-08-02', '-NotesFile', $missing)
    Assert-True 'notes missing: exit 65' ($RC -eq 65)
    Assert-Eq 'notes missing: stdout empty' '' $OUT
    Assert-Eq 'notes missing: message only' "announce: notes file not found: $missing`n" $ERR

    # --- notes file with nothing usable in it is a data error too ---
    $blank = Join-Path $T 'blank.txt'
    [System.IO.File]::WriteAllText($blank, "`n   `n")
    Invoke-Tool @('-Service', 'db-main', '-Date', '2026-08-02', '-NotesFile', $blank)
    Assert-True 'notes blank: exit 65' ($RC -eq 65)
    Assert-Eq 'notes blank: message only' "announce: notes file is empty: $blank`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
