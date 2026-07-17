# netroster.ps1 — render the device roster from the inventory collector's
# devices.csv. The runbook diffs this roster against the vendor's nightly
# export, so the line order has to match the export's documented byte order.
# Usage: pwsh -NoProfile -File netroster.ps1 -Path <devices.csv>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    [Console]::Error.WriteLine("netroster: inventory not found: $Path")
    exit 66
}

$rows = @(Import-Csv -LiteralPath $Path)

Write-Output 'device roster'
$sorted = @($rows | Sort-Object -Property name)
foreach ($row in $sorted) {
    Write-Output ('{0}  {1}  {2}' -f $row.name.PadRight(10), $row.site.PadRight(6), $row.rack)
}
Write-Output ('devices: {0}' -f $rows.Count)
exit 0
