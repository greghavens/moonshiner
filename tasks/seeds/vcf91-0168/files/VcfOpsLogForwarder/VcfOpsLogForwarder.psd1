@{
    RootModule = 'VcfOpsLogForwarder.psm1'
    ModuleVersion = '1.0.0'
    GUID = '5ee75b17-1a57-4ab9-ab70-092bd96cf602'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Contract-driven idempotent VCF Operations for Logs forwarder configuration.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Ops'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Ensure-VcfOpsLogForwarder')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
