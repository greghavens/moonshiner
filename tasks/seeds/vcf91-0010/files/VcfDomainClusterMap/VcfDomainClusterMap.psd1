@{
    RootModule        = 'VcfDomainClusterMap.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '7e679961-29cd-42e9-936c-a2d68cc5bb51'
    Author            = 'VCF Platform Engineering'
    Description       = 'Refresh-safe VCF 9.1 SDDC Manager domain and cluster inventory.'
    PowerShellVersion = '7.2'
    RequiredModules   = @(
        @{
            ModuleName    = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Get-VcfDomainClusterMap')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
