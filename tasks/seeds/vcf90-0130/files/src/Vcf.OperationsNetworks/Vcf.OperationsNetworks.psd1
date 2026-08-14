@{
    RootModule = 'Vcf.OperationsNetworks.psm1'
    ModuleVersion = '1.0.0'
    GUID = '5657a866-5f6e-49dc-b6ed-8b8611f39fc2'
    Author = 'VCF Automation Team'
    CompanyName = 'Community'
    Copyright = '(c) VCF Automation Team. All rights reserved.'
    Description = 'Contract-driven VCF Operations for Networks changes.'
    PowerShellVersion = '7.2'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Ops'
            RequiredVersion = '13.4.0.24798382'
        }
    )
    FunctionsToExport = @('Invoke-VcfOperationsNetworksVcenterChange')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'OperationsForNetworks', 'PowerCLI')
        }
    }
}
