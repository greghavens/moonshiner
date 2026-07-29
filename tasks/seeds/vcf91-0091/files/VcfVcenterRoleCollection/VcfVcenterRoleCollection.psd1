@{
    RootModule = 'VcfVcenterRoleCollection.psm1'
    ModuleVersion = '1.0.0'
    GUID = '50401597-c9ee-4864-aea7-a5ea3a105ea3'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Complete, deterministically ordered vCenter role collection for VCF 9.1.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @(
        'New-VcfVcenterRoleClient'
        'Get-VcfVcenterRoleCollection'
    )
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'vCenter', 'PowerCLI', 'Automation')
        }
    }
}
