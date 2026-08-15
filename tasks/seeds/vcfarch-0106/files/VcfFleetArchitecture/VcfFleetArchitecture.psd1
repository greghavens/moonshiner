@{
    RootModule        = 'VcfFleetArchitecture.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = 'd061f0f6-0ccb-4922-981f-76218178b0dc'
    Author            = 'Platform Architecture'
    Description       = 'Builds a deterministic VCF brownfield fleet migration architecture.'
    PowerShellVersion = '7.2'
    RequiredModules   = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.Installer'; RequiredVersion = '13.5.0.25380678' }
    )
    FunctionsToExport = @('New-VcfFleetMigrationPlan')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
