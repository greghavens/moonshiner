@{
    RootModule        = 'VcfOps.AdapterOnboarding.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = 'b83157f3-1b4d-4131-90e2-bc8b34ff5960'
    Author            = 'VCF Operations automation'
    Description       = 'Precheck-gated adapter instance onboarding for VMware Cloud Foundation Operations 9.1.'
    PowerShellVersion = '7.2'

    # Supplied by the environment. This module never vendors or reimplements the SDK.
    RequiredModules   = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.Ops'; RequiredVersion = '13.5.0.25380678' }
    )

    FunctionsToExport = @('Register-VcfOpsAdapterInstance')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
