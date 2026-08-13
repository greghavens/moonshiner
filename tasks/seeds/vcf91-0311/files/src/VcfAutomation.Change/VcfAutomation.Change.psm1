Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

foreach ($folder in 'Private', 'Public') {
    $dir = Join-Path $PSScriptRoot $folder
    if (-not (Test-Path -LiteralPath $dir)) { continue }
    foreach ($file in Get-ChildItem -LiteralPath $dir -Filter '*.ps1' | Sort-Object Name) {
        . $file.FullName
    }
}

Export-ModuleMember -Function @(
    'Connect-VcfaOrgSession',
    'Initialize-VcfaCatalogItemRequest',
    'Initialize-VcfaResourceActionRequest',
    'Invoke-VcfaCatalogItemChange'
)
