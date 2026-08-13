@{
    RootModule        = 'VcfInstaller.TaskInventory.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '5a1674ce-151f-4d8d-b72b-b58005a96e49'
    Author            = 'Moonshiner task fixture'
    CompanyName       = 'Community'
    Copyright         = '(c) 2026'
    Description       = 'Stable, complete task inventory collection for VCF Installer 9.1.'
    PowerShellVersion = '7.4'
    RequiredModules   = @(
        @{
            ModuleName    = 'VMware.Sdk.Vcf.Installer'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Get-VcfInstallerTaskInventory')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
