@{
    RootModule        = 'VcfAutomation.Change.psm1'
    ModuleVersion     = '0.4.0'
    GUID              = 'c0e9b4a2-7f31-4d58-9a6c-1b2e5d8f3a71'
    Author            = 'Platform Engineering'
    CompanyName       = 'Internal'
    Description       = 'Change-orchestration helpers for the VCF Automation APIs in VMware Cloud Foundation 9.1. VCF PowerCLI 9.1 ships generated SDK bindings for SDDC Manager, Installer, Operations and Cloud Builder but none for VCF Automation, so this module carries the VCF Automation calls itself against the contract transcribed in docs/contract.json. It relies on the installed VCF PowerCLI SDK for bearer-token acquisition and never vendors any part of it.'
    PowerShellVersion = '7.2'

    FunctionsToExport = @(
        'Connect-VcfaOrgSession',
        'Initialize-VcfaCatalogItemRequest',
        'Initialize-VcfaResourceActionRequest',
        'Invoke-VcfaCatalogItemChange'
    )
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()

    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'VCF-Automation', 'VMware', 'REST')

            # Installed by the environment as a prerequisite, never vendored and never
            # bundled with this module. Declared as an external dependency rather than in
            # RequiredModules so that the change orchestration and its tests remain usable
            # with an already-issued bearer token on a host that has no PowerCLI.
            ExternalModuleDependencies = @('VMware.Sdk.Vcf.SddcManager')
        }
    }
}
