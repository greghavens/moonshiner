@{
    RootModule = 'VcfOpsLogForwarder.psm1'
    ModuleVersion = '1.0.0'
    GUID = '8892c845-67b8-494a-b683-1454d59f9a2d'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Contract-driven guarded VCF Operations log-forwarder creation.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Ops'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('New-VcfOpsLogForwarderIfAbsent')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
