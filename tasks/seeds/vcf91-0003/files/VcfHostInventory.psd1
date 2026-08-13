@{
    RootModule        = 'VcfHostInventory.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '9db1da1b-d02f-4b64-8322-e0918e03b7aa'
    Author            = 'Platform Engineering'
    CompanyName       = 'Example'
    Copyright         = '(c) Platform Engineering'
    Description       = 'Stable VMware Cloud Foundation 9.1 SDDC Manager host inventory.'
    PowerShellVersion = '7.2'
    RequiredModules   = @(
        @{
            ModuleName    = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @(
        'Get-VcfHostInventory',
        'Export-VcfHostInventory'
    )
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
