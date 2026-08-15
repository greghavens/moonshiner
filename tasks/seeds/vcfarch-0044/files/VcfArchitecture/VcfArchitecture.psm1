Set-StrictMode -Version Latest

function New-VcfArchitecture {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string] $DesignRequirementsPath,

        [Parameter(Mandatory)]
        [string] $EstateInventoryPath,

        [Parameter(Mandatory)]
        [string] $CompatibilitySnapshotPath,

        [Parameter(Mandatory)]
        [string] $OutputPath
    )

    throw 'Not implemented.'
}

Export-ModuleMember -Function New-VcfArchitecture
