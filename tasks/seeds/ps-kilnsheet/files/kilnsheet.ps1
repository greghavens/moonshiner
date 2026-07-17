# kilnsheet.ps1 -- render the day's firing sheet for the studio kilns.
# The loaders work straight off this printout, so the layout is part of
# the contract: header block, one section per kiln, footer sign-off box.
# Usage: pwsh -NoProfile -File kilnsheet.ps1 -Path <firings.csv>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'kilnprofiles.ps1')

function Get-SheetHeader {
    param([string]$Window)
    $art = @'
==============================
    studio firing sheet
==============================
    '@
    return ('{0}{1}window: {2}' -f $art, "`n", $Window)
}

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    [Console]::Error.WriteLine("kilnsheet: firing log not found: $Path")
    exit 66
}

$rows = @(Import-Csv -LiteralPath $Path)
$kilns = [string[]]@($rows | ForEach-Object { $_.kiln } | Select-Object -Unique)
[Array]::Sort($kilns, [System.StringComparer]::Ordinal)

Write-Output (Get-SheetHeader -Window $Profiles.window)
foreach ($k in $kilns) {
    $p = $Profiles.kilns[$k]
    Write-Output ('== kiln {0} (soak {1} min, {2} shelves) ==' -f $k, $p.soak, $p.shelf)
    $pieces = 0
    foreach ($r in @($rows | Where-Object { $_.kiln -eq $k })) {
        Write-Output ('  {0} x{1} cone {2}' -f $r.item, $r.pieces, $r.cone)
        $pieces += [int]$r.pieces
    }
    Write-Output ('  total pieces: {0}' -f $pieces)
}
$footer = @'
------------------------------
 checked by: ______
'@
Write-Output $footer
exit 0
