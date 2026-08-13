@{
    RootModule        = 'VcfFailureTriage.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = 'e4c1a7d2-90b6-4f18-8f43-2c7d6b5a0e91'
    Author            = 'Moonshiner'
    Description       = 'Diagnoses a failed VCF 9.0 SDDC Manager task from the task itself, the events that name its resources, and a support bundle scoped to what actually failed.'
    PowerShellVersion = '7.4'
    RequiredModules   = @(
        @{
            ModuleName      = 'VMware.Sdk.Vcf.SddcManager'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Invoke-VcfFailureTriage')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
    PrivateData       = @{
        PSData = @{
            Tags = @('VCF', 'SDDCManager', 'Triage', 'SupportBundle')
        }
    }
}
