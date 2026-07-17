# pickbatch.ps1 — print the rush pick list for the morning floor run.
# Reads the order-desk export (orders.json) and lists the open rush orders;
# the pickers work straight off this printout.
# Usage: pwsh -NoProfile -File pickbatch.ps1 -Path <orders.json>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Path
)

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    [Console]::Error.WriteLine("pickbatch: export not found: $Path")
    exit 66
}

function Select-RushOrders {
    param($Orders)
    $picked = [System.Collections.ArrayList]::new()
    foreach ($order in $Orders) {
        if ($order.status -ne 'open') { continue }
        if ($order.priority -eq 'rush') {
            $picked.Add($order)
        }
    }
    Write-Output ('scanned {0} orders' -f $Orders.Count)
    return $picked
}

$orders = @(Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json)
$rush = @(Select-RushOrders -Orders $orders)

Write-Output ('rush orders: {0}' -f $rush.Count)
foreach ($r in $rush) {
    Write-Output (' - {0}  {1} items  bay {2}' -f $r.id, $r.items, $r.bay)
}
exit 0
