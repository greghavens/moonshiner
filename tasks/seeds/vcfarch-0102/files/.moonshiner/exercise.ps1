param(
    [Parameter(Mandatory)]
    [string] $ModulePath,

    [Parameter(Mandatory)]
    [string] $InventoryPath,

    [Parameter(Mandatory)]
    [string] $CompatibilitySnapshotPath,

    [Parameter(Mandatory)]
    [string] $OutputPath
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

Import-Module $ModulePath -Force
$result = New-VcfFleetArchitecture `
    -InventoryPath $InventoryPath `
    -CompatibilitySnapshotPath $CompatibilitySnapshotPath `
    -OutputPath $OutputPath

if (-not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
    throw 'The module did not create the requested artifact.'
}

if ($null -eq $result) {
    throw 'The module did not return its output file.'
}
