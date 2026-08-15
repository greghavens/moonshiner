Set-StrictMode -Version Latest

function New-VcfFleetArchitecture {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string] $InventoryPath,

        [Parameter(Mandatory)]
        [string] $CompatibilitySnapshotPath,

        [Parameter(Mandatory)]
        [ValidateSet('OSA', 'ESA')]
        [string] $StorageArchitecture,

        [Parameter(Mandatory)]
        [string] $OutputDirectory
    )

    throw 'Implement the architecture generator.'
}

Export-ModuleMember -Function New-VcfFleetArchitecture
