@{
    RootModule        = 'VcfOpsAlertInventory.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = 'd41a6b2e-8c07-4f19-9a3b-6e5c2d70f841'
    Author            = 'Platform Engineering'
    CompanyName       = 'Contoso'
    Description       = 'Retrieves the complete VCF Operations 9.0 alert collection in a stable order.'
    PowerShellVersion = '7.2'

    # The public surface is fixed: exactly one exported function and nothing else.
    FunctionsToExport = @('Get-VcfOpsAlertInventory')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()

    # VMware.Sdk.Vcf.Ops is installed by the environment and is the only supported
    # transport for the operations in docs/contract.json. It is never vendored here.
    RequiredModules   = @('VMware.Sdk.Vcf.Ops')
}
