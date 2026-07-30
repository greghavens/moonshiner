@{
    RootModule        = 'VcfNsxPartialRollout.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '6ae3f049-941f-4e42-a5f2-80ab9c8e4e69'
    Author            = 'Platform Engineering'
    CompanyName       = 'Contoso'
    Copyright         = '(c) Platform Engineering'
    Description       = 'Applies and reports a two-step NSX Policy firewall rollout.'
    PowerShellVersion = '7.0'
    RequiredModules   = @(
        @{
            ModuleName    = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Invoke-VcfNsxPartialRollout')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
