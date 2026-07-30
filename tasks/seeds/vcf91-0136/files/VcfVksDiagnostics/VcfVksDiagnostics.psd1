@{
    RootModule = 'VcfVksDiagnostics.psm1'
    ModuleVersion = '1.0.0'
    GUID = '898f35b7-8dc7-472d-a87e-bc30ead3aa47'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Evidence-backed diagnostics for VKS Cluster API failures.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Get-VcfVksFailureDiagnosis')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
