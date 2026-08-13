@{
    RootModule        = 'VcfAutomation.CredentialRotation.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = 'b1c0a6d4-3f27-4e9a-8d51-6a2f7c0e94b3'
    Author            = 'Platform Infrastructure'
    CompanyName       = 'Contoso'
    Description       = 'Rotates VCF Automation cloud account credentials against the VCF 9.1 IaaS API without stranding in-flight requests on the old secret.'
    PowerShellVersion = '7.2'

    # VCF PowerCLI is a prerequisite of this module. It is installed by the
    # environment and is deliberately not vendored into this repository.
    RequiredModules   = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.SddcManager'; ModuleVersion = '13.5.0.0' }
    )

    FunctionsToExport = @(
        'New-VcfaUpdateCloudAccountSpecification',
        'Invoke-VcfaCloudAccountCredentialRotation'
    )
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()

    PrivateData       = @{
        PSData = @{
            Tags = @('VCF', 'VCF-Automation', 'IaaS', 'CredentialRotation')
        }
    }
}
