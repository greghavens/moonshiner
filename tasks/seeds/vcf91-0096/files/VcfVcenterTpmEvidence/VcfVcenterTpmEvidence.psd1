@{
    RootModule = 'VcfVcenterTpmEvidence.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'bea32457-4e47-496f-a4c7-33bb175967db'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Evidence-based vCenter TPM attestation diagnostics for VCF 9.1.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.vSphere'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @(
        'New-VcfVcenterTpmEvidenceClient'
        'Get-VcfHostTpmFailureEvidence'
    )
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'vCenter', 'PowerCLI', 'TPM', 'Diagnostics')
        }
    }
}
