@{
    RootModule           = 'VcfSddcLcm.psm1'
    ModuleVersion        = '1.0.0'
    GUID                 = 'd0f1a4b6-2c58-4e37-9a10-5b7e6c3f8d21'
    Author               = 'VCF Fleet Lifecycle Automation'
    CompanyName          = 'Unknown'
    Copyright            = '(c) VCF Fleet Lifecycle Automation. All rights reserved.'
    Description          = 'SDDC and Fleet lifecycle helpers for VMware Cloud Foundation 9.1, layered on the VMware.Sdk.Vcf PowerCLI modules.'
    PowerShellVersion    = '7.4'

    # Installed by the environment from the PowerShell Gallery. Never vendored
    # into this repository.
    RequiredModules      = @('VMware.Sdk.Vcf.Installer')

    FunctionsToExport    = @('Get-VcfSddcLcmComponentNode')
    CmdletsToExport      = @()
    VariablesToExport    = @()
    AliasesToExport      = @()

    PrivateData          = @{
        PSData = @{
            Tags = @('VCF', 'SDDC', 'LCM', 'Fleet', 'PowerCLI')
        }
    }
}
