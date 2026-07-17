# probefeed.ps1 -- fold synthetic-probe result drops into the ledger.
#
#   pwsh -NoProfile -File probefeed.ps1 -Spool <dir> -Out <ledger.ndjson>
#
# The probe runners drop one small JSON file per result into the spool:
#
#   { "probe": "dns-a", "host": "web01", "status": "ok", "latencyMs": 12 }
#
# Every *.json file in the spool (ordinal name order; other files are not
# ours) is validated and appended to the ledger as one NDJSON line with
# keys in exactly this order:
#
#   {"file":"<name>","probe":...,"host":...,"status":...,"latencyMs":n}
#
# A file that does not parse, or is missing a field, or has an empty
# probe/host, a status other than exactly 'ok'/'fail', or a non-integer
# or negative latencyMs is reported to stderr as
# 'probefeed: skipped <name>: malformed' and left out of the ledger.
#
# stdout is one summary line:  processed <p>, skipped <s>, ok=<a>, fail=<b>
# Exit 0 on any completed run; 66 when the spool directory is missing.

param(
    [Parameter(Mandatory)][string]$Spool,
    [Parameter(Mandatory)][string]$Out
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fail {
    param([string]$Message, [int]$Code)
    [Console]::Error.WriteLine("probefeed: $Message")
    exit $Code
}

function Test-ProbeRecord {
    param($Rec)
    if ($null -eq $Rec) { return $false }
    $names = @($Rec.PSObject.Properties.Name)
    foreach ($required in @('probe', 'host', 'status', 'latencyMs')) {
        if ($names -cnotcontains $required) { return $false }
    }
    if ([string]::IsNullOrEmpty([string]$Rec.probe)) { return $false }
    if ([string]::IsNullOrEmpty([string]$Rec.host)) { return $false }
    if (-not ($Rec.status -cin @('ok', 'fail'))) { return $false }
    $lat = $Rec.latencyMs
    if (-not ($lat -is [int] -or $lat -is [long])) { return $false }
    if ($lat -lt 0) { return $false }
    return $true
}

if (-not (Test-Path -LiteralPath $Spool -PathType Container)) {
    Fail "spool not found: $Spool" 66
}

$names = @()
foreach ($f in @(Get-ChildItem -LiteralPath $Spool -File)) {
    if ($f.Name -clike '*.json') { $names += $f.Name }
}
[Array]::Sort($names, [System.StringComparer]::Ordinal)

$processed = 0
$skipped = 0
$okCount = 0
$failCount = 0

foreach ($name in $names) {
    $raw = [System.IO.File]::ReadAllText((Join-Path $Spool $name))
    $rec = $null
    try {
        $rec = ConvertFrom-Json -InputObject $raw
    } catch {
        $rec = $null
    }
    if (-not (Test-ProbeRecord $rec)) {
        [Console]::Error.WriteLine("probefeed: skipped ${name}: malformed")
        $skipped++
        continue
    }
    $entry = [ordered]@{
        file      = $name
        probe     = [string]$rec.probe
        host      = [string]$rec.host
        status    = [string]$rec.status
        latencyMs = [int]$rec.latencyMs
    }
    $line = ($entry | ConvertTo-Json -Compress) + "`n"
    [System.IO.File]::AppendAllText($Out, $line)
    $processed++
    if ($rec.status -ceq 'ok') { $okCount++ } else { $failCount++ }
}

Write-Output "processed $processed, skipped $skipped, ok=$okCount, fail=$failCount"
exit 0
