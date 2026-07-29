@{
    RootModule = 'VcfNsxIpBlock.psm1'
    ModuleVersion = '1.0.0'
    GUID = '583749c0-11ef-4933-8e68-d33d4df90052'
    Author = 'VCF Automation Team'
    CompanyName = 'Example'
    Copyright = '(c) 2026'
    Description = 'Retry-safe NSX Policy IP address block integration.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @(
        'New-VcfNsxIpAddressBlockModel'
        'Set-VcfNsxIpAddressBlock'
    )
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
