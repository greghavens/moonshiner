@{
    RootModule = 'VcfAutomation.Policy.psm1'
    ModuleVersion = '0.1.0'
    GUID = 'b194c98a-ebae-47e6-b4cc-16ff49fc6eb9'
    Author = 'VCF Automation integration team'
    Description = 'Reference-derived VCF Automation policy REST adapter for VMware.Sdk.Vcf PowerCLI users.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.4.0.24798382'
        }
    )
    FunctionsToExport = @('Set-VcfAutomationPolicy')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
