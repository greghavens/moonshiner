# Acceptance harness for renew.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_renew.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'renew.ps1') -PathType Leaf)) {
    Write-Output 'FAIL renew.ps1 not found in the workspace root'
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
    param([string]$WorkDir, [string[]]$CaseArgs = @())
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    Push-Location -LiteralPath $WorkDir
    try {
        & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'renew.ps1') @CaseArgs 1>$outFile 2>$errFile
        $script:RC = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function New-WorkDir {
    param([string]$Name)
    $d = Join-Path $T $Name
    New-Item -ItemType Directory -Force -Path $d > $null
    return $d
}

# The usage block, byte-exact (stdout for `help`; stderr after every 64 error).
$usage = @'
usage: renew.ps1 <command> [options]

commands:
  init      create renewals.json in the current directory
  add       -Name <cert> -ExpiresOn <yyyy-MM-dd> [-Owner <team>]
  report    [-Before <yyyy-MM-dd>] [-Owner <team>]
  help      print this help

exit codes: 0 ok, 64 usage error, 65 data error
'@ + "`n"

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- init ---
    $W = New-WorkDir 'main'
    Invoke-Tool $W @('init')
    Assert-True 'init: exit 0' ($RC -eq 0)
    Assert-Eq 'init: stdout' "initialized renewals.json`n" $OUT
    Assert-Eq 'init: stderr empty' '' $ERR
    Assert-True 'init: file exists' (Test-Path -LiteralPath (Join-Path $W 'renewals.json') -PathType Leaf)

    Invoke-Tool $W @('init')
    Assert-True 'reinit: exit 65' ($RC -eq 65)
    Assert-Eq 'reinit: message only' "renew: already initialized`n" $ERR
    Assert-Eq 'reinit: stdout empty' '' $OUT

    # --- add: persistence across separate invocations ---
    Invoke-Tool $W @('add', '-Name', 'edge-tls', '-ExpiresOn', '2026-11-05', '-Owner', 'traffic')
    Assert-True 'add 1: exit 0' ($RC -eq 0)
    Assert-Eq 'add 1: stdout' "added edge-tls (expires 2026-11-05)`n" $OUT

    Invoke-Tool $W @('add', '-Name', 'Zeta-cache.internal', '-ExpiresOn', '2026-09-14')
    Assert-True 'add 2: exit 0' ($RC -eq 0)
    Assert-Eq 'add 2: stdout' "added Zeta-cache.internal (expires 2026-09-14)`n" $OUT

    Invoke-Tool $W @('add', '-Name', 'api-signer', '-ExpiresOn', '2026-09-14', '-Owner', 'platform')
    Assert-True 'add 3: exit 0' ($RC -eq 0)

    Invoke-Tool $W @('add', '-Name', 'bastion-host', '-ExpiresOn', '2027-01-02', '-Owner', 'traffic')
    Assert-True 'add 4: exit 0' ($RC -eq 0)

    # --- the store stays valid JSON with an entries array ---
    $store = Get-Content -LiteralPath (Join-Path $W 'renewals.json') -Raw | ConvertFrom-Json -AsHashtable
    Assert-True 'store: entries array present' ($store.Contains('entries'))
    Assert-True 'store: four entries' (@($store['entries']).Count -eq 4)

    # --- report: expiry ascending, name is the ordinal tiebreak ---
    # Same-date pair: 'Zeta-cache.internal' vs 'api-signer' — ordinal puts
    # the uppercase Z first; a culture-linguistic sort would swap them.
    Invoke-Tool $W @('report')
    Assert-True 'report all: exit 0' ($RC -eq 0)
    Assert-Eq 'report all: rows' "2026-09-14 Zeta-cache.internal -`n2026-09-14 api-signer platform`n2026-11-05 edge-tls traffic`n2027-01-02 bastion-host traffic`n" $OUT
    Assert-Eq 'report all: stderr empty' '' $ERR

    # --- report -Before is strictly-before (the boundary date is excluded) ---
    Invoke-Tool $W @('report', '-Before', '2026-11-05')
    Assert-Eq 'report before: rows' "2026-09-14 Zeta-cache.internal -`n2026-09-14 api-signer platform`n" $OUT

    # --- report -Owner is an exact match ---
    Invoke-Tool $W @('report', '-Owner', 'traffic')
    Assert-Eq 'report owner: rows' "2026-11-05 edge-tls traffic`n2027-01-02 bastion-host traffic`n" $OUT

    Invoke-Tool $W @('report', '-Owner', 'Traffic')
    Assert-True 'report owner case: exit 0' ($RC -eq 0)
    Assert-Eq 'report owner case: no rows' '' $OUT

    # --- both filters combine ---
    Invoke-Tool $W @('report', '-Before', '2027-01-02', '-Owner', 'traffic')
    Assert-Eq 'report combined: rows' "2026-11-05 edge-tls traffic`n" $OUT

    # --- duplicates are refused, case-sensitively ---
    Invoke-Tool $W @('add', '-Name', 'edge-tls', '-ExpiresOn', '2027-03-01')
    Assert-True 'dup: exit 65' ($RC -eq 65)
    Assert-Eq 'dup: message' "renew: duplicate certificate: edge-tls`n" $ERR

    Invoke-Tool $W @('add', '-Name', 'Edge-tls', '-ExpiresOn', '2027-03-01')
    Assert-True 'dup case: exit 0 (different name)' ($RC -eq 0)

    # --- add validation ---
    Invoke-Tool $W @('add', '-Name', 'bad name', '-ExpiresOn', '2026-12-01')
    Assert-True 'bad name: exit 65' ($RC -eq 65)
    Assert-Eq 'bad name: message' "renew: invalid certificate name: bad name`n" $ERR

    Invoke-Tool $W @('add', '-Name', 'ok-cert', '-ExpiresOn', '01-12-2026')
    Assert-True 'bad date: exit 65' ($RC -eq 65)
    Assert-Eq 'bad date: message' "renew: invalid date: 01-12-2026`n" $ERR

    Invoke-Tool $W @('add', '-Name', 'ok-cert')
    Assert-True 'add missing opts: exit 64' ($RC -eq 64)
    Assert-Eq 'add missing opts: message + usage' ("renew: add: -Name and -ExpiresOn are required`n" + $usage) $ERR
    Assert-Eq 'add missing opts: stdout empty' '' $OUT

    Invoke-Tool $W @('add', '-Name', 'ok-cert', '-ExpiresOn')
    Assert-True 'opt no value: exit 64' ($RC -eq 64)
    Assert-Eq 'opt no value: message + usage' ("renew: option -ExpiresOn requires a value`n" + $usage) $ERR

    Invoke-Tool $W @('add', '-Name', 'ok-cert', '-ExpiresOn', '2026-12-01', '-Priority', 'high')
    Assert-True 'unknown opt: exit 64' ($RC -eq 64)
    Assert-Eq 'unknown opt: message + usage' ("renew: unknown option: -Priority`n" + $usage) $ERR

    Invoke-Tool $W @('add', '-Name', 'ok-cert', '-ExpiresOn', '2026-12-01', 'stray')
    Assert-True 'add stray: exit 64' ($RC -eq 64)
    Assert-Eq 'add stray: message + usage' ("renew: add: unexpected argument: stray`n" + $usage) $ERR

    Invoke-Tool $W @('report', '-Before', 'soon')
    Assert-True 'report bad date: exit 65' ($RC -eq 65)
    Assert-Eq 'report bad date: message' "renew: invalid date: soon`n" $ERR

    Invoke-Tool $W @('report', 'everything')
    Assert-True 'report stray: exit 64' ($RC -eq 64)
    Assert-Eq 'report stray: message + usage' ("renew: report: unexpected argument: everything`n" + $usage) $ERR

    # --- running before init is a data error ---
    $fresh = New-WorkDir 'fresh'
    Invoke-Tool $fresh @('add', '-Name', 'x-cert', '-ExpiresOn', '2026-12-01')
    Assert-True 'add before init: exit 65' ($RC -eq 65)
    Assert-Eq 'add before init: message' "renew: not initialized (run 'renew.ps1 init' first)`n" $ERR

    Invoke-Tool $fresh @('report')
    Assert-True 'report before init: exit 65' ($RC -eq 65)
    Assert-Eq 'report before init: message' "renew: not initialized (run 'renew.ps1 init' first)`n" $ERR

    # --- empty report on a fresh store ---
    Invoke-Tool $fresh @('init')
    Assert-True 'fresh init: exit 0' ($RC -eq 0)
    Invoke-Tool $fresh @('report')
    Assert-True 'empty report: exit 0' ($RC -eq 0)
    Assert-Eq 'empty report: no rows' '' $OUT

    # --- dispatcher-level errors ---
    Invoke-Tool $W @()
    Assert-True 'no command: exit 64' ($RC -eq 64)
    Assert-Eq 'no command: message + usage' ("renew: missing command`n" + $usage) $ERR

    Invoke-Tool $W @('renewals')
    Assert-True 'unknown command: exit 64' ($RC -eq 64)
    Assert-Eq 'unknown command: message + usage' ("renew: unknown command: renewals`n" + $usage) $ERR

    Invoke-Tool $W @('help')
    Assert-True 'help: exit 0' ($RC -eq 0)
    Assert-Eq 'help: usage on stdout' $usage $OUT
    Assert-Eq 'help: stderr empty' '' $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
