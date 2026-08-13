@{
    RootModule        = 'VcfOpsReporting.psm1'
    ModuleVersion     = '0.1.0'
    GUID              = '3b7c1e64-5a2f-4d18-9f6b-0c4e7a91d2b3'
    Author            = 'VCF Operations Platform Engineering'
    Description       = 'On-demand report generation against the VCF Operations API, built on VMware.Sdk.Vcf.Ops.'
    PowerShellVersion = '7.0'

    # Supplied by the environment as a prerequisite. Never vendored into this repository.
    RequiredModules   = @('VMware.Sdk.Vcf.Ops')

    FunctionsToExport = @(
        'Connect-VcfOpsReportingSession',
        'Start-VcfOpsReportGeneration',
        'Wait-VcfOpsReportGeneration',
        'Save-VcfOpsReport',
        'Invoke-VcfOpsReportRun'
    )
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
