@{
    RootModule        = 'VcfOpsInventory.psm1'
    ModuleVersion     = '0.1.0'
    GUID              = 'd41f5b2a-6c07-4f88-9a3e-71b0c5e2a904'
    Author            = 'Platform Automation'
    CompanyName       = 'Platform Automation'
    Description       = 'Resource inventory snapshots from VMware Cloud Foundation Operations 9.1.'
    PowerShellVersion = '7.2'

    # VMware.Sdk.Vcf.Ops is an environment prerequisite. It is required here so
    # that importing this module fails loudly if it is missing, rather than
    # failing later on the first cmdlet call.
    RequiredModules   = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.Ops'; ModuleVersion = '13.5.0.25380678' }
    )

    FunctionsToExport = @('Get-VcfOpsResourceInventory')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
