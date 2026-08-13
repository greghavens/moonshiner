@{
    RootModule           = 'VcfOpsNetworks.DataSource.psm1'
    ModuleVersion        = '1.0.0'
    GUID                 = '0f3c1c5e-9a4b-4a8e-9d21-2b6f5c7e4a10'
    Author               = 'VCF Automation'
    CompanyName          = 'VCF Automation'
    Copyright            = '(c) VCF Automation. All rights reserved.'
    Description          = 'Onboards vCenter data sources into VCF Operations for Networks (VCF 9.1) behind a mandatory validation precheck.'
    PowerShellVersion    = '7.4'

    # Supplied by the environment as a prerequisite. The seed never vendors,
    # copies, or re-implements any part of the VMware.Sdk.Vcf PowerCLI stack.
    RequiredModules      = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.Ops'; RequiredVersion = '13.5.0.25380678' }
    )

    FunctionsToExport    = @('Add-VcfNetworksVcenterDataSource')
    CmdletsToExport      = @()
    VariablesToExport    = @()
    AliasesToExport      = @()

    PrivateData          = @{
        PSData = @{
            Tags       = @('VCF', 'VCF-9.1', 'Operations-for-Networks', 'NetworkInsight')
            LicenseUri = 'https://www.apache.org/licenses/LICENSE-2.0'
        }
        Contract = @{
            Path       = 'docs/contract.json'
            Repository = 'vmware/vcf-api-specs'
            CommitSha  = 'c3f3b52c845dd967cabbc21680e893292077d5ba'
            SpecPath   = 'specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml'
        }
    }
}
