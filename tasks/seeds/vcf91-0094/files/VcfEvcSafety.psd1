@{
    RootModule        = 'VcfEvcSafety.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '15a23804-7583-4a95-b3b9-5b27be9e48bf'
    Author            = 'VCF Platform Engineering'
    CompanyName       = 'Example'
    Copyright         = '(c) 2026 Example'
    Description       = 'Spec-derived, precheck-gated vCenter EVC updates for VCF 9.1.'
    PowerShellVersion = '7.4'
    RequiredModules   = @(
        @{
            ModuleName    = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Set-VcfClusterEvcModeSafely')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
