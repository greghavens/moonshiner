@{
    RootModule = 'VcfVksNamespaceInventory.psm1'
    ModuleVersion = '1.0.0'
    GUID = '8496f2d5-f3aa-4b42-8d6a-76ab4ddf3a08'
    Author = 'Platform Engineering'
    Description = 'Joins authorized vSphere Supervisor namespaces with VKS Cluster API inventory.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.vSphere'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @(
        'New-VcfVksNamespaceSession'
        'Get-VcfVksClusterInventory'
    )
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
