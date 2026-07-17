# depotops.ps1 -- spare-parts depot operations toolbox.
#
# One script, three commands, used by the depot crew every morning:
#
#   pwsh -NoProfile -File depotops.ps1 report    -Stock stock.csv
#   pwsh -NoProfile -File depotops.ps1 shortfall -Stock stock.csv -Orders orders.csv
#   pwsh -NoProfile -File depotops.ps1 audit     -Stock stock.csv [-Orders orders.csv]
#
# stock.csv  columns: sku,desc,qty,min   (qty/min integers)
# orders.csv columns: order,sku,qty     (qty integer)
#
# report    -- one line per stock row, ordinal by sku:
#                  <sku> qty=<qty> min=<min> <ok|LOW>
#              LOW means qty is strictly below min.
# shortfall -- reserve every open order against stock, then list skus whose
#              available (qty - reserved) is below min, ordinal by sku:
#                  <sku> need=<min - available>
# audit     -- consistency findings, one per line, sorted ordinal:
#                  dup-sku <sku> / dup-order <order> / neg-qty <sku> /
#                  neg-min <sku> / unknown-sku <order> <sku>
#              exit 65 when there is at least one finding, 0 when clean.
#
# Exit codes: 0 ok, 64 usage or malformed input, 65 findings / unknown sku,
# 66 missing input file.

param(
    [Parameter(Position = 0)]
    [string]$Command,
    [string]$Stock,
    [string]$Orders
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-ToolError {
    param([string]$Message)
    [Console]::Error.WriteLine("depotops: $Message")
}

function Test-IntText {
    param([string]$Value)
    $parsed = 0
    return [int]::TryParse($Value, [ref]$parsed)
}

function Read-CsvRows {
    # Load a CSV and make sure the expected columns are all present.
    param([string]$Path, [string[]]$Columns)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Write-ToolError "file not found: $Path"
        exit 66
    }
    $rows = @(Import-Csv -LiteralPath $Path)
    if ($rows.Count -gt 0) {
        $have = @($rows[0].PSObject.Properties.Name)
        foreach ($col in $Columns) {
            if ($have -cnotcontains $col) {
                $leaf = Split-Path -Leaf $Path
                Write-ToolError "${leaf}: missing column '$col'"
                exit 64
            }
        }
    }
    return $rows
}

function ConvertTo-StockRecords {
    # Validate raw stock rows and lift them into typed records.
    param([object[]]$Rows, [string]$Leaf)
    $records = @()
    $line = 1   # the header sits on line 1
    foreach ($row in $Rows) {
        $line++
        if ([string]::IsNullOrEmpty($row.sku) -or
            -not (Test-IntText $row.qty) -or
            -not (Test-IntText $row.min)) {
            Write-ToolError "${Leaf}: bad row $line"
            exit 64
        }
        $records += [pscustomobject]@{
            Sku  = [string]$row.sku
            Desc = [string]$row.desc
            Qty  = [int]$row.qty
            Min  = [int]$row.min
        }
    }
    return $records
}

function ConvertTo-OrderRecords {
    param([object[]]$Rows, [string]$Leaf)
    $records = @()
    $line = 1
    foreach ($row in $Rows) {
        $line++
        if ([string]::IsNullOrEmpty($row.order) -or
            [string]::IsNullOrEmpty($row.sku) -or
            -not (Test-IntText $row.qty)) {
            Write-ToolError "${Leaf}: bad row $line"
            exit 64
        }
        $records += [pscustomobject]@{
            Order = [string]$row.order
            Sku   = [string]$row.sku
            Qty   = [int]$row.qty
        }
    }
    return $records
}

function Sort-StockRecords {
    # Ordinal by sku -- Sort-Object is culture-sensitive, so do it by hand.
    param([object[]]$Records)
    $list = [System.Collections.Generic.List[object]]::new()
    foreach ($r in $Records) { [void]$list.Add($r) }
    $list.Sort([System.Comparison[object]]{
        param($a, $b)
        [string]::CompareOrdinal($a.Sku, $b.Sku)
    })
    return $list.ToArray()
}

function Sort-OrdinalLines {
    param([string[]]$Lines)
    $copy = @($Lines)
    [Array]::Sort($copy, [System.StringComparer]::Ordinal)
    return $copy
}

function Get-ReportLines {
    param([object[]]$StockRecords)
    $lines = @()
    foreach ($r in @(Sort-StockRecords $StockRecords)) {
        $status = if ($r.Qty -lt $r.Min) { 'LOW' } else { 'ok' }
        $lines += "$($r.Sku) qty=$($r.Qty) min=$($r.Min) $status"
    }
    return $lines
}

function Get-ShortfallLines {
    param([object[]]$StockRecords, [object[]]$OrderRecords)
    $known = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
    foreach ($r in $StockRecords) { [void]$known.Add($r.Sku) }

    $unknown = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
    $reserved = [System.Collections.Generic.Dictionary[string, int]]::new([System.StringComparer]::Ordinal)
    foreach ($o in $OrderRecords) {
        if (-not $known.Contains($o.Sku)) {
            [void]$unknown.Add($o.Sku)
            continue
        }
        if ($reserved.ContainsKey($o.Sku)) {
            $reserved[$o.Sku] = $reserved[$o.Sku] + $o.Qty
        } else {
            $reserved[$o.Sku] = $o.Qty
        }
    }
    if ($unknown.Count -gt 0) {
        $bad = @(Sort-OrdinalLines @($unknown))
        Write-ToolError "unknown sku in orders: $($bad[0])"
        exit 65
    }

    $lines = @()
    foreach ($r in @(Sort-StockRecords $StockRecords)) {
        $held = if ($reserved.ContainsKey($r.Sku)) { $reserved[$r.Sku] } else { 0 }
        $available = $r.Qty - $held
        if ($available -lt $r.Min) {
            $lines += "$($r.Sku) need=$($r.Min - $available)"
        }
    }
    return $lines
}

function Get-AuditLines {
    param([object[]]$StockRecords, [object[]]$OrderRecords)
    $lines = @()

    $skuCount = [System.Collections.Generic.Dictionary[string, int]]::new([System.StringComparer]::Ordinal)
    foreach ($r in $StockRecords) {
        if ($skuCount.ContainsKey($r.Sku)) {
            $skuCount[$r.Sku] = $skuCount[$r.Sku] + 1
        } else {
            $skuCount[$r.Sku] = 1
        }
        if ($r.Qty -lt 0) { $lines += "neg-qty $($r.Sku)" }
        if ($r.Min -lt 0) { $lines += "neg-min $($r.Sku)" }
    }
    foreach ($sku in $skuCount.Keys) {
        if ($skuCount[$sku] -gt 1) { $lines += "dup-sku $sku" }
    }

    if ($null -ne $OrderRecords) {
        $known = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
        foreach ($r in $StockRecords) { [void]$known.Add($r.Sku) }
        $orderCount = [System.Collections.Generic.Dictionary[string, int]]::new([System.StringComparer]::Ordinal)
        foreach ($o in $OrderRecords) {
            if ($orderCount.ContainsKey($o.Order)) {
                $orderCount[$o.Order] = $orderCount[$o.Order] + 1
            } else {
                $orderCount[$o.Order] = 1
            }
            if (-not $known.Contains($o.Sku)) {
                $lines += "unknown-sku $($o.Order) $($o.Sku)"
            }
        }
        foreach ($id in $orderCount.Keys) {
            if ($orderCount[$id] -gt 1) { $lines += "dup-order $id" }
        }
    }

    return (Sort-OrdinalLines $lines)
}

function Read-StockRecords {
    param([string]$Path)
    $rows = @(Read-CsvRows -Path $Path -Columns @('sku', 'desc', 'qty', 'min'))
    return (ConvertTo-StockRecords -Rows $rows -Leaf (Split-Path -Leaf $Path))
}

function Read-OrderRecords {
    param([string]$Path)
    $rows = @(Read-CsvRows -Path $Path -Columns @('order', 'sku', 'qty'))
    return (ConvertTo-OrderRecords -Rows $rows -Leaf (Split-Path -Leaf $Path))
}

if ([string]::IsNullOrEmpty($Command)) {
    Write-ToolError 'usage: depotops.ps1 <report|shortfall|audit> -Stock <file> [-Orders <file>]'
    exit 64
}
if ($Command -cne 'report' -and $Command -cne 'shortfall' -and $Command -cne 'audit') {
    Write-ToolError "unknown command: $Command"
    exit 64
}
if ([string]::IsNullOrEmpty($Stock)) {
    Write-ToolError '-Stock is required'
    exit 64
}

switch -CaseSensitive ($Command) {
    'report' {
        $records = @(Read-StockRecords -Path $Stock)
        foreach ($line in @(Get-ReportLines $records)) { Write-Output $line }
        exit 0
    }
    'shortfall' {
        if ([string]::IsNullOrEmpty($Orders)) {
            Write-ToolError '-Orders is required for shortfall'
            exit 64
        }
        $records = @(Read-StockRecords -Path $Stock)
        $open = @(Read-OrderRecords -Path $Orders)
        foreach ($line in @(Get-ShortfallLines $records $open)) { Write-Output $line }
        exit 0
    }
    'audit' {
        $records = @(Read-StockRecords -Path $Stock)
        $open = $null
        if (-not [string]::IsNullOrEmpty($Orders)) {
            $open = @(Read-OrderRecords -Path $Orders)
        }
        $findings = @(Get-AuditLines $records $open)
        foreach ($line in $findings) { Write-Output $line }
        if ($findings.Count -gt 0) { exit 65 }
        exit 0
    }
}
