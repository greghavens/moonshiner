@{
    RootModule = 'VcfOpsLogForwarder.psm1'
    ModuleVersion = '1.0.0'
    GUID = '703e1efd-5b8b-4c11-96f5-9dc2b8555827'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Contract-driven VCF Operations Log Management forwarder reconciliation.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Ops'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Sync-VcfOpsLogForwarder')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
