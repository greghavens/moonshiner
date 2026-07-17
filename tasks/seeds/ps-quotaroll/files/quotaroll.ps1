# quotaroll.ps1 — per-team storage quota roll-up from the nightly usage export.
# The export job on the filer drops usage.csv (columns: user,team,used,extra);
# this script renders one team's roll-up for the capacity channel.
# Usage: pwsh -NoProfile -File quotaroll.ps1 -Path <usage.csv> -Team <code>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$Team
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    [Console]::Error.WriteLine("quotaroll: usage export not found: $Path")
    exit 66
}

$rows = @(Import-Csv -LiteralPath $Path)

# team codes come straight from the directory; roll up the requested team only
$members = @($rows | Where-Object { $_.team -eq $Team })
$members = @($members | Sort-Object -Property user)

Write-Output "quota roll: team $Team"
$grand = 0
foreach ($m in $members) {
    $total = $m.used + $m.extra
    Write-Output ('{0,-12} {1,8} GB' -f $m.user, $total)
    $grand += $total
}
Write-Output ("team total: {0} GB" -f $grand)
Write-Output ("members: {0}" -f $members.Count)
exit 0
