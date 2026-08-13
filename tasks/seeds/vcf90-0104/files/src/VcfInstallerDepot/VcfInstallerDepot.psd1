@{
    RootModule        = 'VcfInstallerDepot.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '93f35e21-167c-4c08-a2fb-421cf868550e'
    Author            = 'VCF Automation Team'
    CompanyName       = 'Example'
    Copyright         = '(c) VCF Automation Team'
    Description       = 'Retry-safe helpers for the VMware Cloud Foundation Installer API.'
    PowerShellVersion = '7.0'
    RequiredModules   = @(
        @{
            ModuleName      = 'VMware.OpenAPI'
            RequiredVersion = '13.4.0.24798382'
        }
        @{
            ModuleName      = 'VMware.Sdk.Vcf.Installer'
            RequiredVersion = '13.4.0.24798382'
        }
    )
    FunctionsToExport = @('Set-VcfInstallerDepotToken')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
