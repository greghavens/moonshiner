@{
    RootModule = 'VcfArchitecture.psm1'
    ModuleVersion = '1.0.0'
    GUID = '7f62606f-f386-4faf-a230-b1559f877a10'
    Author = 'VCF Architecture Team'
    Description = 'Builds machine-checkable VCF 9.1 greenfield and migration architecture artifacts.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Installer'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @(
        'New-VcfGreenfieldSddcSpec'
        'New-VcfMigrationPlan'
    )
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
