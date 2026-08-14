@{
    RootModule = 'VcfAutomation.Integrations.psm1'
    ModuleVersion = '0.1.0'
    GUID = '45ec26a5-c89a-47a9-8381-418f2a2af492'
    Author = 'Platform Automation Team'
    Description = 'VCF Automation integration helpers for VCF PowerCLI.'
    PowerShellVersion = '7.4'
    CompatiblePSEditions = @('Core')
    RequiredModules = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.SddcManager' }
    )
    FunctionsToExport = @('New-VcfAutomationIntegration')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
