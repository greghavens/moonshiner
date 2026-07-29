@{
    RootModule        = 'VcfNsxGroupInventory.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '16375fe4-aec2-4ac2-9605-e5d5b2f4d993'
    Author            = 'Platform Engineering'
    CompanyName       = 'Contoso'
    Copyright         = '(c) Platform Engineering'
    Description       = 'Retrieves a complete, deterministic NSX Policy group inventory.'
    PowerShellVersion = '7.0'
    RequiredModules   = @(
        @{
            ModuleName    = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Get-VcfNsxPolicyGroupInventory')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
