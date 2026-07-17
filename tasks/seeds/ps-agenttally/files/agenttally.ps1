# agenttally.ps1 — per-agent check-in tally for the fleet dashboard.
# checkins.csv comes from the enrolment gateway, one row per check-in
# (agent,seq). Agent ids are the opaque tokens the gateway issued.
# Usage: pwsh -NoProfile -File agenttally.ps1 -Path <checkins.csv> [-Mute id[,id]]
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Path,
    [string[]]$Mute = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    [Console]::Error.WriteLine("agenttally: feed not found: $Path")
    exit 66
}

$rows = @(Import-Csv -LiteralPath $Path)

$counts = @{}
foreach ($row in $rows) {
    if ($Mute -contains $row.agent) { continue }
    if (-not $counts.ContainsKey($row.agent)) { $counts[$row.agent] = 0 }
    $counts[$row.agent] = $counts[$row.agent] + 1
}

$ids = [string[]]@($counts.Keys)
[Array]::Sort($ids, [System.StringComparer]::Ordinal)

Write-Output 'agent check-in tally'
foreach ($id in $ids) {
    Write-Output ('{0}  {1}' -f $id.PadRight(8), $counts[$id])
}
Write-Output ('agents: {0}' -f $ids.Count)
exit 0
