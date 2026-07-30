@{
    RootModule = 'VcfVcenterPrivilegeInventory.psm1'
    ModuleVersion = '1.0.0'
    GUID = '8f695ac9-a8ec-435d-aa14-9248d882e34e'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Resumable vCenter privilege inventory using the VCF PowerCLI SDK.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.vSphere'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @(
        'New-VcfVcenterPrivilegeInventorySession'
        'Get-VcfVcenterPrivilegeInventory'
    )
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'vCenter', 'PowerCLI')
        }
    }
}
