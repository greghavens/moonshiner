@{
    RootModule = 'VcfFleetArchitecture.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'd29f42ee-8843-4d09-917a-f0c2a81ce102'
    Author = 'Platform Architecture'
    Description = 'Builds a VCF 9.1 brownfield fleet architecture artifact.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.Installer'; RequiredVersion = '13.5.0.25380678' }
    )
    FunctionsToExport = @('New-VcfFleetArchitecture')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
