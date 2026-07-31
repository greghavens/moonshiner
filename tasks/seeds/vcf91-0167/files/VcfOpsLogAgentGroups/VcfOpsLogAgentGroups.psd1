@{
    RootModule = 'VcfOpsLogAgentGroups.psm1'
    ModuleVersion = '1.0.0'
    GUID = '4892f627-1491-4e33-9c55-fb75a4f7b5d3'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Contract-driven VCF Operations for Logs agent-group inventory.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Ops'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Get-VcfOpsLogAgentGroupInventory')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
