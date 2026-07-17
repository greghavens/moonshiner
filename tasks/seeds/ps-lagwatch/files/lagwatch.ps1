# lagwatch.ps1 — replication lag report for the on-call pager.
# The collector snapshots per-replica lag to lag.csv (replica,lag_seconds);
# this renders the report worst-first and exits 65 when anything is at or
# over the limit so cron pages.
# Usage: pwsh -NoProfile -File lagwatch.ps1 -Path <lag.csv> [-LimitSeconds <n>]
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Path,
    [int]$LimitSeconds = 300
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    [Console]::Error.WriteLine("lagwatch: snapshot not found: $Path")
    exit 66
}

$rows = @(Import-Csv -LiteralPath $Path)

Write-Output ('lag report (limit {0}s)' -f $LimitSeconds)
$alerts = 0
$sorted = @($rows | Sort-Object -Property lag_seconds -Descending)
foreach ($row in $sorted) {
    if ($row.lag_seconds -ge $LimitSeconds) {
        $alerts++
        Write-Output ('!! {0} lag {1}s' -f $row.replica, $row.lag_seconds)
    } else {
        Write-Output ('ok {0} lag {1}s' -f $row.replica, $row.lag_seconds)
    }
}
Write-Output ('alerts: {0}' -f $alerts)
if ($alerts -gt 0) { exit 65 }
exit 0
