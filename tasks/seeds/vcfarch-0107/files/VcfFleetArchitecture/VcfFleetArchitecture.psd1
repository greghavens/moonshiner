@{
    RootModule = 'VcfFleetArchitecture.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'cc00a7f1-5a77-4eeb-8a23-1cd4aeb3b732'
    Author = 'Northwind Architecture'
    Description = 'Generates VCF 9.1 greenfield and brownfield fleet architecture artifacts.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Installer'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('New-VcfFleetArchitecture')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
