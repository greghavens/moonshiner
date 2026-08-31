@{
    RootModule = 'VcfVcenterCloneInventory.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'f66daf58-e3ca-485d-9250-66ff3b2ab54c'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Polls an asynchronous vCenter clone before returning stable VM inventory.'
    PowerShellVersion = '7.4'
    RequiredModules = @()
    FunctionsToExport = @(
        'New-VcfVcenterCloneInventoryClient'
        'Invoke-VcfVcenterCloneInventory'
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
