@{
    RootModule        = 'VcfResilientDomains.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '672a228d-3d83-4a43-aa15-a5e68618269c'
    Author            = 'VCF Platform Engineering'
    Description       = 'Resilient VCF 9.1 SDDC Manager domain inventory export.'
    PowerShellVersion = '7.2'
    RequiredModules   = @(
        @{
            ModuleName    = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Export-VcfResilientDomainInventory')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
