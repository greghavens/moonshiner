@{
    RootModule        = 'VcfSddcLcm.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '6f1b4a2e-9c74-4f38-9a1d-2b7e5c0d8a13'
    Author            = 'VCF Platform Engineering'
    Description       = 'Retry-safe SDDC LCM lifecycle operations for VMware Cloud Foundation 9.1.'
    PowerShellVersion = '7.2'

    # Supplied by the environment (PSGallery); never vendored into this repo.
    RequiredModules   = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.SddcManager'; ModuleVersion = '13.5.0' }
    )

    FunctionsToExport = @('New-VcfSddcLcmSession', 'Start-VcfSddcLcmSupportBundle')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()

    PrivateData       = @{
        PSData = @{
            Tags       = @('VCF', 'VMware', 'SDDC', 'LCM', 'Lifecycle')
            LicenseUri = 'https://www.apache.org/licenses/LICENSE-2.0'
        }
    }
}
