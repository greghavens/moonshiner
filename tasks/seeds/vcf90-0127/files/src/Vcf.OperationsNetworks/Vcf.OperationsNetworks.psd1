@{
    RootModule = 'Vcf.OperationsNetworks.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'ec8980e6-aee7-4774-a36c-ac6286a476da'
    Author = 'VCF Automation Team'
    Description = 'Contract-based VCF Operations for Networks batch helpers for VCF 9.0.'
    PowerShellVersion = '7.0'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Ops'
            RequiredVersion = '13.4.0.24798382'
        }
    )
    FunctionsToExport = @('Add-VcfOperationsNetworksVCenterBatch')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
