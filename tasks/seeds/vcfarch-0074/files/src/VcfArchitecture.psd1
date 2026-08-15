@{
    RootModule = 'VcfArchitecture.psm1'
    ModuleVersion = '1.0.0'
    GUID = '13d3c30f-f83d-45bf-a4d4-f3239c601daf'
    Author = 'VCF Architecture Team'
    CompanyName = 'Example'
    Copyright = '(c) Example'
    Description = 'Generates a validated brownfield VMware Cloud Foundation migration architecture.'
    PowerShellVersion = '7.0'
    RequiredModules = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.Installer'; RequiredVersion = '13.5.0.25380678' }
        @{ ModuleName = 'VMware.Sdk.Vcf.SddcManager'; RequiredVersion = '13.5.0.25380678' }
    )
    FunctionsToExport = @('New-VcfMigrationArchitecture')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
