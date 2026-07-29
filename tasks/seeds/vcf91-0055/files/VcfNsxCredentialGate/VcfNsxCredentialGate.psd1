@{
    RootModule        = 'VcfNsxCredentialGate.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '96fc6149-fb97-4e47-9257-3478a5df0a55'
    Author            = 'Platform Engineering'
    CompanyName       = 'Contoso'
    Copyright         = '(c) Platform Engineering'
    Description       = 'Drains NSX Policy requests before a credential cutover.'
    PowerShellVersion = '7.0'
    RequiredModules   = @(
        @{
            ModuleName    = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @(
        'New-VcfNsxCredentialGate',
        'Get-VcfNsxGroupPage',
        'Set-VcfNsxCredential'
    )
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
