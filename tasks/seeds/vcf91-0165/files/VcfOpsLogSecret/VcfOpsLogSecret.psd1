@{
    RootModule = 'VcfOpsLogSecret.psm1'
    ModuleVersion = '1.0.0'
    GUID = '1b61a7b1-9bd0-4da5-a6bb-b983ac8f1941'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Contract-driven VCF Operations for Logs agent-secret activation.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Ops'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('New-VcfOpsLogAgentSecretAndWait')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
