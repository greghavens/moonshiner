# render.ps1 -- print the report lines the yard office pins up.
function Format-Stock {
    param([Parameter(Mandatory)][object[]]$Entries)
    $reorder = 0
    foreach ($e in $Entries) {
        Write-Output ('{0} [{1}] bay {2}: {3} -> {4}' -f $e.row.sku, $e.row.kind, $e.row.bay, $e.row.count, $e.status)
        if ($e.status -eq 'reorder') { $reorder++ }
    }
    Write-Output ('reorder now: {0} of {1} lines' -f $reorder, $Entry.Count)
}
