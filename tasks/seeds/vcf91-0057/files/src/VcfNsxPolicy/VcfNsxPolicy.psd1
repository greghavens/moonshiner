@{
    RootModule = 'VcfNsxPolicy.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'c2a842c2-7360-4387-a12e-aa029c948e57'
    Author = 'VCF automation team'
    CompanyName = 'Community'
    Copyright = '(c) VCF automation team'
    Description = 'Focused NSX Policy segment realization client for VCF 9.1.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0'
        }
    )
    FunctionsToExport = @(
        'New-VcfNsxPolicyClient'
        'Get-VcfNsxPolicySegment'
        'Set-VcfNsxPolicySegment'
    )
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'NSX', 'Policy', 'PowerCLI')
        }
    }
}
