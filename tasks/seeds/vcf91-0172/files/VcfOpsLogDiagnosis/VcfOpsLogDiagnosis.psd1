@{
    RootModule = 'VcfOpsLogDiagnosis.psm1'
    ModuleVersion = '1.0.0'
    GUID = '7043d781-b8e2-4b5f-9765-8aa8e888b5e2'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Evidence-driven VCF Operations for Logs incident diagnosis.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Ops'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Get-VcfOpsIncidentDiagnosis')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
