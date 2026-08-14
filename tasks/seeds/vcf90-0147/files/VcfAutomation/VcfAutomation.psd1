@{
    RootModule = 'VcfAutomation.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'b81fdfe0-54c2-44e7-9a5a-90147090cf09'
    Author = 'VCF Automation Engineering'
    CompanyName = 'Example'
    Copyright = '(c) VCF Automation Engineering'
    Description = 'Project update helpers for VMware Cloud Foundation Automation 9.0.'
    PowerShellVersion = '7.0'
    CompatiblePSEditions = @('Core')
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Installer'
            ModuleVersion = '13.4.0.24798382'
        }
    )
    FunctionsToExport = @('Sync-VcfAutomationProject')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'Automation', 'PowerCLI')
        }
    }
}
