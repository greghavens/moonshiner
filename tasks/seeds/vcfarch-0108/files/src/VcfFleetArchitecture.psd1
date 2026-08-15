@{
    RootModule = 'VcfFleetArchitecture.psm1'
    ModuleVersion = '0.1.0'
    GUID = '541c701d-c421-4b38-9cb4-1550671e0987'
    Author = 'Northwind Architecture'
    Description = 'Builds a pinned, machine-readable VCF brownfield fleet migration architecture.'
    PowerShellVersion = '7.2'
    RequiredModules = @('VMware.Sdk.Vcf.Installer', 'VMware.Sdk.Vcf.SddcManager')
    FunctionsToExport = @('New-VcfFleetArchitecture', 'Test-VcfFleetInstallerSpec')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
