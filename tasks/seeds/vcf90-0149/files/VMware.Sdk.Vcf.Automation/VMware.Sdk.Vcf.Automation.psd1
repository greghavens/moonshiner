@{
    RootModule = 'VMware.Sdk.Vcf.Automation.psm1'
    ModuleVersion = '1.0.0'
    GUID = '06bb45a4-b7cb-48bc-8421-565d3af378d2'
    Author = 'VCF Automation Integration Team'
    CompanyName = 'Community'
    Copyright = '(c) VCF Automation Integration Team'
    Description = 'Reference-derived VCF Automation bindings that follow the VMware.Sdk.Vcf PowerCLI module conventions.'
    PowerShellVersion = '7.2'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.4.0.24798382'
        }
    )
    FunctionsToExport = @('Invoke-VcfAutomationUpdateProject')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'Automation', 'PowerCLI')
            ProjectUri = 'https://developer.broadcom.com/powercli'
        }
    }
}
