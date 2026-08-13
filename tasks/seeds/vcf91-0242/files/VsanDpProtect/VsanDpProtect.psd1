@{
    RootModule           = 'VsanDpProtect.psm1'
    ModuleVersion        = '1.0.0'
    GUID                 = '2f4c9a61-7d38-4b52-9c0e-6ab1d5f83e47'
    Author               = 'VMware Cloud Foundation 9.1 automation'
    Description          = 'Creates a vSAN Data Protection protection group and a manual protection group snapshot over the pinned Snapshot Appliance API contract, refreshing an expired session token without repeating completed work.'
    PowerShellVersion    = '7.4'
    CompatiblePSEditions = @('Core')
    FunctionsToExport    = @('New-VsanDpProtectedSnapshot')
    CmdletsToExport      = @()
    VariablesToExport    = @()
    AliasesToExport      = @()
    PrivateData          = @{
        PSData = @{
            Tags = @('VCF', 'vSAN', 'DataProtection', 'Snapservice')
        }
        Contract = @{
            Repository = 'https://github.com/vmware/vcf-api-specs'
            CommitSha  = 'c3f3b52c845dd967cabbc21680e893292077d5ba'
            SpecPath   = 'specifications/vsan-data-protection/vsan-data-protection-openapi.yaml'
        }
    }
}
