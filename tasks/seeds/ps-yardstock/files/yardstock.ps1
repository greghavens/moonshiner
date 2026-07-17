# yardstock.ps1 -- morning stock report for the timber yard.
# Usage: pwsh -NoProfile -File yardstock.ps1 -Path <stock.csv> [-Threshold 12]
param(
    [Parameter(Mandatory)][string]$Path
    [int]$Threshold = 12
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/readers.ps1')
. (Join-Path $PSScriptRoot 'lib/rules.ps1')
. (Join-Path $PSScriptRoot 'lib/render.ps1')

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    [Console]::Error.WriteLine("yardstock: stock file not found: $Path")
    exit 66
}

$rows = Read-StockRows -Path $Path
$entries = Get-StockStatus -Rows $rows -Threshold $Threshold
Format-Stock -Entries $entries
exit 0
