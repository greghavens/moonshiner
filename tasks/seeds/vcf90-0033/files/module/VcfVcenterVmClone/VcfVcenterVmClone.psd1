@{
    RootModule        = 'VcfVcenterVmClone.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = 'd41a9c7e-52b8-4f60-9a13-6c8e0b7d24f5'
    Author            = 'VMware Cloud Foundation automation'
    Description       = 'Virtual machine cloning against the vSphere Automation API for vCenter in VMware Cloud Foundation 9.0.'
    PowerShellVersion = '7.2'

    # Supplied by the environment as a prerequisite; never vendored by this module.
    # Importing this manifest also loads the shared VMware.OpenAPI runtime that
    # carries the VMware.Binding.OpenApi client types.
    RequiredModules   = @('VMware.Sdk.Vcf.SddcManager')

    FunctionsToExport = @('Connect-VcfVcenterApi', 'New-VcfVcenterVmClone')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
