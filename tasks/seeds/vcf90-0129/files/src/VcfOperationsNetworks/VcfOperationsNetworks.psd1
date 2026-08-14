@{
    RootModule = 'VcfOperationsNetworks.psm1'
    ModuleVersion = '1.0.0'
    GUID = '8c25f377-8b57-4bb0-9ec7-9f2de6f28d25'
    Author = 'VCF Automation Team'
    Description = 'Contract-driven helpers for VCF Operations for Networks 9.0.'
    PowerShellVersion = '7.2'
    CompatiblePSEditions = @('Core')
    RequiredModules = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.Ops'; ModuleVersion = '13.4.0' }
    )
    FunctionsToExport = @('Ensure-VcfNetworkApplication')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
