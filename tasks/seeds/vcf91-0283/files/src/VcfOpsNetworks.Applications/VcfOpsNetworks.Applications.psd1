@{
    RootModule        = 'VcfOpsNetworks.Applications.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = 'b1f0a2c4-6d3e-4a17-9c58-2e7d41f0a8b3'
    Author            = 'VCF Automation'
    CompanyName       = 'VCF Automation'
    Description       = 'Bulk save of discovered applications in VCF Operations for Networks (VCF 9.1), driven by the VMware.Sdk.Vcf PowerCLI OpenAPI binding layer.'
    PowerShellVersion = '7.4'

    # Installed by the environment as a prerequisite. Never vendored into this repository.
    RequiredModules   = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.Ops'; ModuleVersion = '13.5.0.25380678' }
    )

    FunctionsToExport = @('Save-VcfOnDiscoveredApplication')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()

    PrivateData       = @{
        PSData = @{
            Tags = @('VCF', 'VCF-Operations-for-Networks', 'PowerCLI')
        }
        Contract = @{
            Path       = 'docs/contract.json'
            Sources    = 'docs/official_sources.json'
        }
    }
}
