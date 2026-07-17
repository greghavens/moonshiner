# rules.ps1 -- stock level policy: reorder under threshold, watch under triple.
function Get-StockStatus {
    param(
        [Parameter(Mandatory)][object[]]$Rows,
        [Parameter(Mandatory)][int]$Threshold
    )
    $out = @()
    foreach ($r in $Rows) {
        $status = if ($r.count -lt $Threshold) {
            'reorder'
        } else if ($r.count -lt 3 * $Threshold) {
            'watch'
        } else {
            'ok'
        }
        $out += [pscustomobject]@{ row = $r; status = $status }
    }
    return ,$out
}
