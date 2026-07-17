# loadsheet.ps1 -- freight load-sheet helpers for the northbound runs.
#
# Dot-source this file, then:
#
#   $rows = @(Read-LoadRows -Path loads.csv)
#   Format-LoadSheet -Rows $rows -Title 'northbound am'
#
# loads.csv columns: id,dest,kg,pri   (kg/pri integers)

Set-StrictMode -Version Latest

function Read-LoadRows {
    # Parse the dispatch CSV. One load = one tab-joined record:
    # id, dest, kg, pri -- every helper downstream splits on tab.
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "file not found: $Path"
    }
    $leaf = Split-Path -Leaf $Path
    $rows = @()
    $line = 1   # the header sits on line 1
    foreach ($rec in @(Import-Csv -LiteralPath $Path)) {
        $line++
        $kg = 0
        $pri = 0
        if ([string]::IsNullOrEmpty($rec.id) -or
            -not [int]::TryParse($rec.kg, [ref]$kg) -or
            -not [int]::TryParse($rec.pri, [ref]$pri)) {
            throw "${leaf}: bad row $line"
        }
        $rows += (($rec.id, $rec.dest, $rec.kg, $rec.pri) -join "`t")
    }
    return $rows
}

function Select-HeavyLoads {
    # Keep the loads at or above the weight cutoff.
    param([string[]]$Rows, [Parameter(Mandatory)][int]$MinKg)
    $out = @()
    foreach ($row in $Rows) {
        $parts = $row -split "`t"
        if ([int]$parts[2] -ge $MinKg) { $out += $row }
    }
    return $out
}

function Select-Destination {
    # Keep the loads bound for one destination (exact, case-sensitive).
    param([string[]]$Rows, [Parameter(Mandatory)][string]$Dest)
    $out = @()
    foreach ($row in $Rows) {
        $parts = $row -split "`t"
        if ($parts[1] -ceq $Dest) { $out += $row }
    }
    return $out
}

function Measure-Loads {
    # Totals for a set of loads: count, total kg, max kg -- tab-joined
    # like everything else in here.
    param([string[]]$Rows)
    $count = 0
    $total = 0
    $max = 0
    foreach ($row in $Rows) {
        $parts = $row -split "`t"
        $kg = [int]$parts[2]
        $count++
        $total += $kg
        if ($kg -gt $max) { $max = $kg }
    }
    return (($count, $total, $max) -join "`t")
}

function Format-LoadSheet {
    # Render the printable sheet the yard crew works from. Returns one
    # string; the caller decides where it goes.
    param([string[]]$Rows, [Parameter(Mandatory)][string]$Title)
    $lines = @("LOAD SHEET: $Title")
    if ($null -eq $Rows -or $Rows.Count -eq 0) {
        $lines += 'no loads'
        return (($lines -join "`n") + "`n")
    }
    $lines += ('id'.PadRight(10) + 'dest'.PadRight(20) + 'kg'.PadLeft(6) + 'pri'.PadLeft(5))
    foreach ($row in $Rows) {
        $parts = $row -split "`t"
        $lines += ($parts[0].PadRight(10) + $parts[1].PadRight(20) +
                   $parts[2].PadLeft(6) + $parts[3].PadLeft(5))
    }
    $summary = (Measure-Loads -Rows $Rows) -split "`t"
    $lines += "total $($summary[0]) loads, $($summary[1]) kg"
    return (($lines -join "`n") + "`n")
}
