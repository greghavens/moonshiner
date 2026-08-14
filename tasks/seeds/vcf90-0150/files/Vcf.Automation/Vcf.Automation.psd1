@{
    RootModule = 'Vcf.Automation.psm1'
    ModuleVersion = '0.1.0'
    GUID = 'c8e4bf52-6975-4cf4-aad1-b4bf67e3790a'
    Author = 'VCF Automation Operations'
    CompanyName = 'Community'
    Copyright = '(c) 2026'
    Description = 'Reference-derived VCF Automation 9.0 deployment operations for VCF PowerCLI.'
    PowerShellVersion = '7.4'
    CompatiblePSEditions = @('Core')
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.4.0.24798382'
        }
    )
    FunctionsToExport = @('Invoke-VcfAutomationDeploymentChange')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'VCFAutomation', 'PowerCLI', 'REST')
            ProjectUri = 'https://developer.broadcom.com/xapis/vm-apps-org-policies/9.0/'
        }
    }
}
