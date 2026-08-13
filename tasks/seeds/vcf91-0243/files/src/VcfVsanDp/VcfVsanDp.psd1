@{
    RootModule        = 'VcfVsanDp.psm1'
    ModuleVersion     = '0.1.0'
    GUID              = 'e3c1a7d2-58b4-4f6a-9c0e-2d7b81f45a63'
    Author            = 'Platform Automation'
    CompanyName       = 'Contoso'
    Description       = 'PowerShell bindings for the VCF 9.1 vSAN Data Protection (snapservice) API.'
    PowerShellVersion = '7.2'

    # TODO: the VMware.Sdk.Vcf modules are installed in this environment and must
    # not be vendored into this repository.

    FunctionsToExport = @()
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()

    PrivateData = @{
        PSData = @{
            Tags       = @('VCF', 'vSAN', 'DataProtection', 'Snapservice')
            ProjectUri = 'https://github.com/contoso/vcf-vsan-dp'
        }
    }
}
