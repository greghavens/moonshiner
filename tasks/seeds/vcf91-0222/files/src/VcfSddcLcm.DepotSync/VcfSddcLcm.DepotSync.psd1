@{
    RootModule = 'VcfSddcLcm.DepotSync.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'b2d9a4c1-6f37-4e58-9a20-71c8e5d3f0ab'
    Author = 'Moonshiner'
    CompanyName = 'Moonshiner'
    Copyright = '(c) 2026'
    Description = 'Fleet depot registration and component resolution against VCF 9.1 SDDC LCM, with mid-run access token refresh.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Invoke-VcfSddcLcmDepotSync')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
