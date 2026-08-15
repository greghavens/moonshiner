Set-StrictMode -Version Latest

function New-VcfMigrationArchitecture {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $InventoryPath,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $CompatibilitySnapshotPath,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $OutputPath
    )

    throw 'New-VcfMigrationArchitecture has not been implemented.'
}

Export-ModuleMember -Function New-VcfMigrationArchitecture
