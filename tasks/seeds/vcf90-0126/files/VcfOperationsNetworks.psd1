@{
    RootModule = 'VcfOperationsNetworks.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'e858f44a-2e59-43e3-9a3f-82094479f04c'
    Author = 'Platform Engineering'
    Description = 'Focused VCF Operations for Networks integrations for VCF 9.0.'
    PowerShellVersion = '7.0'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Ops'
            RequiredVersion = '13.4.0.24798382'
        }
    )
    FunctionsToExport = @('Invoke-VcfOperationsNetworksCertificateUpdate')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
