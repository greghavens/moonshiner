@{
    RootModule        = 'VcfOpsNetworksApp.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = 'e7c94a15-3d62-48b0-9a77-1f5c8b0d2e64'
    Author            = 'Cloud Platform Operations'
    CompanyName       = 'Cloud Platform Operations'
    Copyright         = '(c) Cloud Platform Operations'
    Description       = 'Retry-safe application onboarding for VMware Cloud Foundation 9.1 Operations for Networks.'
    PowerShellVersion = '7.4'

    RequiredModules   = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.Ops'; RequiredVersion = '13.5.0.25380678' }
    )

    FunctionsToExport = @('New-VcfOpsNetworksApplication')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
