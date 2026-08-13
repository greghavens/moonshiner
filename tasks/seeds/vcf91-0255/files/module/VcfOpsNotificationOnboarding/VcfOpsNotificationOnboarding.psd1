@{
    RootModule        = 'VcfOpsNotificationOnboarding.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '7f1c3e58-9a04-4d2b-8c67-e35b0a94d1f2'
    Author            = 'VMware Cloud Foundation automation'
    Description       = 'Onboards an outbound alert plugin instance and its notification rule in VMware Cloud Foundation Operations 9.1.'
    PowerShellVersion = '7.2'

    # Supplied by the environment as a prerequisite; never vendored by this module.
    # Importing this manifest also loads the shared VMware.OpenAPI runtime that
    # carries the VMware.Binding.OpenApi client types.
    RequiredModules   = @('VMware.Sdk.Vcf.Ops')

    FunctionsToExport = @('Connect-VcfOpsNotificationSession', 'New-VcfOpsNotificationBinding')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
