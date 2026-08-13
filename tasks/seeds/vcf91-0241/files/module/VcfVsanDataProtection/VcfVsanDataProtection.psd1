@{
    RootModule        = 'VcfVsanDataProtection.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '2b7c4d19-0f36-4a58-9c21-7e8d5a3b6c04'
    Author            = 'VMware Cloud Foundation automation'
    Description       = 'On-demand vSAN Data Protection protection group snapshots for VMware Cloud Foundation 9.1.'
    PowerShellVersion = '7.2'

    # Supplied by the environment as a prerequisite; never vendored by this module.
    # Importing this manifest also loads the shared VMware.OpenAPI runtime that
    # carries the VMware.Binding.OpenApi client types.
    RequiredModules   = @('VMware.Sdk.Vcf.SddcManager')

    FunctionsToExport = @('Connect-VsanDpAppliance', 'New-VsanDpProtectionGroupSnapshot')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
