@{
    RootModule        = 'VcfFailureEvidence.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '2fae23cb-84d6-42b4-a4b2-eccf02d69953'
    Author            = 'Moonshiner'
    Description       = 'Correlates VCF SDDC Manager failure evidence with targeted support logs.'
    PowerShellVersion = '7.4'
    RequiredModules   = @(
        @{
            ModuleName      = 'VMware.Sdk.Vcf.SddcManager'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Get-VcfFailureEvidence')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
    PrivateData       = @{
        PSData = @{
            Tags = @('VCF', 'SDDCManager', 'Diagnostics')
        }
    }
}
