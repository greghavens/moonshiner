@{
    RootModule = 'VcfVcenterRoleInventory.psm1'
    ModuleVersion = '1.0.0'
    GUID = '5f663d5b-3ee0-4a49-a43d-e585507521d7'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Resumable vCenter authorization-role inventory using the VCF PowerCLI SDK.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.vSphere'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @(
        'New-VcfVcenterRoleInventorySession'
        'Get-VcfVcenterRoleInventory'
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
