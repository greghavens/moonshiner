@{
    RootModule           = 'VcfOpsNetworks.psm1'
    ModuleVersion        = '9.1.0.0'
    GUID                 = 'b4d2f2b0-9d3a-4f1e-8a77-3c0e5c6d21a4'
    Author               = 'VCF Automation'
    CompanyName          = 'Example'
    Description          = 'Companion PowerShell module for the VCF Operations for Networks API surface that the VMware.Sdk.Vcf PowerCLI modules do not cover.'
    PowerShellVersion    = '7.4'

    # The VMware.Sdk.Vcf PowerCLI modules are an environment prerequisite. They
    # are never vendored into this repository. Importing this module fails if
    # the prerequisite is absent. VMware.OpenAPI carries the
    # VMware.Binding.OpenApi transport that every VMware.Sdk.Vcf.* module uses.
    RequiredModules      = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.Ops'; ModuleVersion = '13.5.0' },
        @{ ModuleName = 'VMware.OpenAPI'; ModuleVersion = '13.5.0' }
    )

    FunctionsToExport    = @('Connect-VcfOnServer', 'Get-VcfOnApplication')
    CmdletsToExport      = @()
    VariablesToExport    = @()
    AliasesToExport      = @()

    PrivateData          = @{
        PSData = @{
            Tags = @('VCF', 'VCF-Operations-for-Networks', 'PowerCLI')
        }
    }
}
