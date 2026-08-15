@{
    RootModule = 'VcfBrownfieldPlanner.psm1'
    ModuleVersion = '1.0.0'
    GUID = '9bc9d706-9df7-4aa8-b667-967dbeec2d42'
    Author = 'Rainpole Architecture'
    Description = 'Produces deterministic VCF brownfield migration plans and can collect live inventory through VCF PowerCLI.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.SddcManager'; ModuleVersion = '9.1.0.0' }
    )
    FunctionsToExport = @(
        'Get-VcfSdkEstateInventory',
        'New-VcfMigrationPlan',
        'Export-VcfMigrationPlan'
    )
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
