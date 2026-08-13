@{
    RootModule        = 'VcfOpsCollectionTriage.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = 'b0f1c4d2-6a35-4f88-9c31-7e2a5d0b4c19'
    Author            = 'Cloud Platform Operations'
    CompanyName       = 'Cloud Platform Operations'
    Copyright         = '(c) Cloud Platform Operations'
    Description       = 'Triage a stalled VMware Cloud Foundation 9.1 Operations collection from alert and symptom evidence.'
    PowerShellVersion = '7.4'

    RequiredModules   = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.Ops'; RequiredVersion = '13.5.0.25380678' }
    )

    FunctionsToExport = @('Get-VcfOpsCollectionDiagnosis')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
