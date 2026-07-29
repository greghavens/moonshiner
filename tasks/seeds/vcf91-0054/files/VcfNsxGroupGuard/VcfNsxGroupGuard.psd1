@{
    RootModule        = 'VcfNsxGroupGuard.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = 'a30bfd61-232d-47bd-867c-4cbac9a70054'
    Author            = 'Platform Engineering'
    CompanyName       = 'Contoso'
    Copyright         = '(c) Platform Engineering'
    Description       = 'Guards an NSX Policy group rename with a revision precheck.'
    PowerShellVersion = '7.0'
    RequiredModules   = @(
        @{
            ModuleName    = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Set-VcfNsxGroupDisplayName')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
