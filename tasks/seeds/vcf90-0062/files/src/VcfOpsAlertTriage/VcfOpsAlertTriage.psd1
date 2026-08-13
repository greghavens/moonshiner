@{
    RootModule           = 'VcfOpsAlertTriage.psm1'
    ModuleVersion        = '0.1.0'
    GUID                 = 'b0c1a54e-6f2d-4d0b-9d3a-1f6f8f2c7a41'
    Author               = 'Cloud Operations Engineering'
    CompanyName          = 'Contoso'
    Description          = 'Alert triage sweep for VMware Cloud Foundation Operations 9.0, driven by the VMware.Sdk.Vcf.Ops PowerCLI module.'
    PowerShellVersion    = '7.2'
    RequiredModules      = @('VMware.Sdk.Vcf.Ops')
    FunctionsToExport    = @('Invoke-VcfOpsAlertTriage')
    CmdletsToExport      = @()
    VariablesToExport    = @()
    AliasesToExport      = @()
    PrivateData          = @{
        PSData = @{
            Tags = @('VCF', 'VCFOperations', 'PowerCLI')
        }
    }
}
