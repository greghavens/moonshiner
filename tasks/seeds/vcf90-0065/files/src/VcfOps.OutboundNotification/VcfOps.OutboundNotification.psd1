@{
    RootModule        = 'VcfOps.OutboundNotification.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = 'd41c9b62-7e08-4a5f-9c3d-6b2f8a1e50d7'
    Author            = 'Moonshiner task fixture'
    CompanyName       = 'Community'
    Copyright         = '(c) 2026'
    Description       = 'Outbound notification onboarding for VCF Operations 9.0.'
    PowerShellVersion = '7.4'
    RequiredModules   = @(
        @{
            ModuleName    = 'VMware.Sdk.Vcf.Ops'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('New-VcfOpsOutboundNotification')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
