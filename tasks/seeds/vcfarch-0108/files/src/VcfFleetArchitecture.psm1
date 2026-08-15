Set-StrictMode -Version Latest

function New-VcfFleetArchitecture {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $InventoryPath,
        [Parameter(Mandatory)] [string] $CompatibilitySnapshotPath,
        [Parameter(Mandatory)] [string] $OutputPath
    )

    throw 'Implement the inventory-driven VCF fleet architecture generator.'
}

function Test-VcfFleetInstallerSpec {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ArchitecturePath
    )

    throw 'Implement online VCF Installer SddcSpec validation.'
}

Export-ModuleMember -Function New-VcfFleetArchitecture, Test-VcfFleetInstallerSpec
