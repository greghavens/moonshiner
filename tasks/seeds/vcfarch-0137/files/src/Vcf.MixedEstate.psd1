@{
    RootModule = 'Vcf.MixedEstate.psm1'
    ModuleVersion = '1.0.0'
    GUID = '7b827d39-4896-4e8a-96f1-bbdf6b7b0137'
    Author = 'VCF Architecture Team'
    Description = 'Builds a machine-checkable VCF mixed-estate migration architecture.'
    PowerShellVersion = '7.2'
    RequiredModules = @('VMware.Sdk.Vcf.Installer')
    FunctionsToExport = @('New-VcfMixedEstatePlan')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
