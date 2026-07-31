@{
    RootModule = 'VcfOpsLogRouting.psm1'
    ModuleVersion = '1.0.0'
    GUID = '276f0d8b-1327-49ca-810b-154e78f90cc1'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Contract-driven VCF Operations for Logs routing changes.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Ops'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Invoke-VcfOpsLogRoutingChange')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
