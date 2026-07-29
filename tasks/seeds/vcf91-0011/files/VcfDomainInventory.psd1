@{
    RootModule        = 'VcfDomainInventory.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '251e1180-e9cf-4fc4-91fe-e3a96410f655'
    Author            = 'Platform Engineering'
    CompanyName       = 'Example'
    Copyright         = '(c) Platform Engineering'
    Description       = 'Stable VMware Cloud Foundation 9.1 SDDC Manager domain inventory.'
    PowerShellVersion = '7.2'
    RequiredModules   = @('VMware.Sdk.Vcf.SddcManager')
    FunctionsToExport = @(
        'Get-VcfDomainInventory',
        'Export-VcfDomainInventory'
    )
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
