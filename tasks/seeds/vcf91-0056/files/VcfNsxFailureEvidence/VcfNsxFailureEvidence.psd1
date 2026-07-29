@{
    RootModule        = 'VcfNsxFailureEvidence.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '27b70aec-eaea-4cf0-a0df-17955f49ab4f'
    Author            = 'Platform Engineering'
    CompanyName       = 'Contoso'
    Copyright         = '(c) Platform Engineering'
    Description       = 'Correlates an NSX Policy intent status with realization alarm evidence.'
    PowerShellVersion = '7.0'
    RequiredModules   = @(
        @{
            ModuleName    = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Get-VcfNsxIntentFailureEvidence')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
