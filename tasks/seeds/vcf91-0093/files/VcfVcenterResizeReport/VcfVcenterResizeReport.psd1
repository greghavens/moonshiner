@{
    RootModule = 'VcfVcenterResizeReport.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'fef5c24a-8b18-479f-9f87-9571bc91ba97'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Partial-failure reporting for a vCenter VM resize and start workflow.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @(
        'New-VcfVcenterResizeClient'
        'Set-VcfVmResizeAndStart'
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
