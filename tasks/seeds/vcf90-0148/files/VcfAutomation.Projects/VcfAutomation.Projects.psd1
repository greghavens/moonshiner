@{
    RootModule = 'VcfAutomation.Projects.psm1'
    ModuleVersion = '1.0.0'
    GUID = '40a96be6-9a0c-474c-8427-d94ac74223d6'
    Author = 'VCF Automation Engineering'
    CompanyName = 'Example'
    Copyright = '(c) VCF Automation Engineering'
    Description = 'Complete, stably ordered VCF Automation 9.0 project inventory.'
    PowerShellVersion = '7.0'
    CompatiblePSEditions = @('Core')
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Installer'
            ModuleVersion = '13.4.0.24798382'
        }
    )
    FunctionsToExport = @('Get-VcfAutomationProjectInventory')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'Automation', 'PowerCLI')
        }
    }
}
