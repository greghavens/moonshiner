Set-StrictMode -Version Latest

function New-VcfGreenfieldSddcSpec {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $RequirementsPath,
        [Parameter(Mandatory)] [string] $OutputPath
    )

    throw 'Not implemented'
}

function New-VcfMigrationPlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $InventoryPath,
        [Parameter(Mandatory)] [string] $CompatibilityPath,
        [Parameter(Mandatory)] [string] $OutputPath
    )

    throw 'Not implemented'
}

Export-ModuleMember -Function New-VcfGreenfieldSddcSpec, New-VcfMigrationPlan
