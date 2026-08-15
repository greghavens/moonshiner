#Requires -Version 7.2
#Requires -Modules @{ ModuleName = 'VMware.Sdk.Vcf.Installer'; ModuleVersion = '13.5.0.25380678' }
#Requires -Modules @{ ModuleName = 'VMware.Sdk.Vcf.SddcManager'; ModuleVersion = '13.5.0.25380678' }

Set-StrictMode -Version Latest

function New-VcfMixedEstateArchitecture {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string] $InventoryPath,

        [Parameter(Mandatory)]
        [string] $CompatibilityPath,

        [Parameter(Mandatory)]
        [string] $OutputPath
    )

    throw 'TODO: build the mixed-estate architecture'
}

Export-ModuleMember -Function New-VcfMixedEstateArchitecture
