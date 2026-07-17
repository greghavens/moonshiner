# readers.ps1 -- load the yard's stock ledger into typed rows.
function Read-StockRows {
    param([Parameter(Mandatory)][string]$Path)
    $rows = @()
    foreach ($r in @(Import-Csv -LiteralPath $Path) {
        $rows += [pscustomobject]@{
            sku   = $r.sku
            kind  = $r.kind
            count = [int]$r.count
            bay   = $r.bay
        }
    }
    Write-Verbose "loaded "$rows.Count" rows"
    return ,$rows
}
