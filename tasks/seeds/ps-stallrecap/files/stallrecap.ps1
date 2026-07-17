# stallrecap.ps1 -- end-of-day recap for the market stalls.
# Sales CSV columns: stall,item,qty,unit,kind (kind is sale or refund).
# Stall registry: JSON array of {id, owner, note?}; note is optional.
# Usage: pwsh -NoProfile -File stallrecap.ps1 -Sales <sales.csv> -Stalls <stalls.json>
param(
    [Parameter(Mandatory)][string]$Sales,
    [Parameter(Mandatory)][string]$Stalls
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Sales -PathType Leaf)) {
    [Console]::Error.WriteLine("stallrecap: sales file not found: $Sales")
    exit 66
}
if (-not (Test-Path -LiteralPath $Stalls -PathType Leaf)) {
    [Console]::Error.WriteLine("stallrecap: stall registry not found: $Stalls")
    exit 66
}

$rows = @(Import-Csv -LiteralPath $Sales)
$registry = @(Get-Content -LiteralPath $Stalls -Raw | ConvertFrom-Json)

$ids = [string[]]@($registry | ForEach-Object { $_.id })
[Array]::Sort($ids, [System.StringComparer]::Ordinal)

$refundRows = @($rows | Where-Object { $_.kind -eq 'refund' }).Count

Write-Output ('market day recap ({0} stalls)' -f $registry.Count)
foreach ($id in $ids) {
    $s = @($registry | Where-Object { $_.id -eq $id })[0]
    Write-Output ('== stall {0} (owner {1}) ==' -f $s.id, $s.owner)
    if ($s.note) {
        Write-Output ('  note: {0}' -f $s.note)
    }
    $stallRows = @($rows | Where-Object { $_.stall -eq $id })
    $sold = 0
    $take = 0
    $refunds = 0
    foreach ($r in $stallRows) {
        if ($r.kind -eq 'refund') { $refunds++; continue }
        $sold += [int]$r.qty
        $take += [int]$r.qty * [int]$r.unit
    }
    $bulk = $stallRows | Where-Object { $_.kind -eq 'sale' -and [int]$_.qty -ge 10 }
    Write-Output ('  items sold: {0}' -f $sold)
    Write-Output ('  take: {0}' -f $take)
    Write-Output ('  bulk lines: {0}' -f $bulk.Count)
    Write-Output ('  refunds: {0}' -f $refunds)
    $grandTake += $take
}
Write-Output ('day take: {0}' -f $grandTake)
Write-Output ('refund rows: {0}' -f $refundRows)
exit 0
