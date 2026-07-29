@{
    RootModule = 'VcfVCenterAutomation.psm1'
    ModuleVersion = '1.0.0'
    GUID = '39e3b3d8-1dad-44aa-b9db-b1d597f236ef'
    Author = 'VCF automation team'
    CompanyName = 'Community'
    Copyright = '(c) VCF automation team'
    Description = 'Focused asynchronous vCenter Automation API client for VCF 9.1.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0'
        }
    )
    FunctionsToExport = @(
        'New-VcfVCenterClient'
        'Invoke-VcfVmClone'
    )
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'vCenter', 'Automation', 'PowerCLI')
        }
    }
}
