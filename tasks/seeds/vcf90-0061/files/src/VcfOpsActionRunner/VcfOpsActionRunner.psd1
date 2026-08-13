@{
    RootModule        = 'VcfOpsActionRunner.psm1'
    ModuleVersion     = '0.1.0'
    GUID              = '4a9d3f21-6c58-4b0e-9a77-2f1c8d5b0e33'
    Author            = 'Cloud Platform Automation'
    CompanyName       = 'Internal'
    Description       = 'Runs VMware Cloud Foundation Operations actions to a terminal state on top of the VMware.Sdk.Vcf.Ops PowerCLI client.'
    PowerShellVersion = '7.2'

    # VMware.Sdk.Vcf.Ops is an environment prerequisite and is deliberately not
    # vendored into this repository. VcfOpsActionRunner.psm1 imports it and
    # fails loudly if it is missing.
    FunctionsToExport = @('Invoke-VcfOpsActionAndWait')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()

    PrivateData       = @{
        PSData = @{
            Tags = @('VCF', 'VCF-Operations', 'PowerCLI')
        }
    }
}
