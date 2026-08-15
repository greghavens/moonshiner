Set-StrictMode -Version Latest

function New-VcfMixedEstatePlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string] $InventoryPath,

        [Parameter(Mandatory)]
        [string] $OutputPath
    )

    throw 'Implement the mixed-estate architecture generator.'
}

Export-ModuleMember -Function New-VcfMixedEstatePlan
