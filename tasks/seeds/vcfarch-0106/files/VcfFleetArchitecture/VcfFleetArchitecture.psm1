Set-StrictMode -Version Latest

function New-VcfFleetMigrationPlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string] $InventoryPath,

        [Parameter(Mandatory)]
        [string] $CompatibilitySnapshotPath,

        [Parameter(Mandatory)]
        [string] $OutputPath
    )

    throw 'The brownfield fleet architecture has not been implemented.'
}

Export-ModuleMember -Function New-VcfFleetMigrationPlan
