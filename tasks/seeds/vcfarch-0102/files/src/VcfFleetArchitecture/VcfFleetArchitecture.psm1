Set-StrictMode -Version Latest

function New-VcfFleetArchitecture {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string] $InventoryPath,

        [Parameter(Mandatory)]
        [string] $CompatibilitySnapshotPath,

        [Parameter(Mandatory)]
        [string] $OutputPath
    )

    throw 'Not implemented.'
}

Export-ModuleMember -Function New-VcfFleetArchitecture
