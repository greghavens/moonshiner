@{
    RootModule        = 'VcfOpsAlertTriage.psm1'
    ModuleVersion     = '0.1.0'
    GUID              = 'd6f0c8a1-4b73-4e29-9f15-3ca8b27e0d64'
    Author            = 'Cloud Operations'
    Description       = 'Nightly alert triage run against VCF Operations, built on VMware.Sdk.Vcf.Ops.'
    PowerShellVersion = '7.2'
    FunctionsToExport = @('Invoke-VcfOpsAlertTriage')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
