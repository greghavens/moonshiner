@{
    RootModule = 'VcfInstaller.Depot.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'c0e71d4b-aee4-42f3-88d7-2b22685b9fb1'
    Author = 'Moonshiner'
    CompanyName = 'Moonshiner'
    Copyright = '(c) 2026'
    Description = 'Idempotent VCF Installer depot settings integration.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Installer'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Set-VcfInstallerDepotToken')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
