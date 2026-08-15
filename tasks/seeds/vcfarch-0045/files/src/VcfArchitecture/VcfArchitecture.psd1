@{
    RootModule = 'VcfArchitecture.psm1'
    ModuleVersion = '1.0.0'
    GUID = '5ab60c03-1d73-49cb-98ed-b72ebfa85d5e'
    Author = 'Architecture Engineering'
    Description = 'Generates the pinned VCF 9.0 greenfield and estate migration architecture artifacts.'
    PowerShellVersion = '7.4'
    CompatiblePSEditions = @('Core')
    RequiredModules = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.Installer'; RequiredVersion = '13.4.0.24798382' }
    )
    FunctionsToExport = @(
        'New-VcfGreenfieldSddcSpec',
        'New-VcfEstateMigrationPlan',
        'Export-VcfArchitecture'
    )
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
