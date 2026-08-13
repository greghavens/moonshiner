@{
    RootModule        = 'VcfAutomationDeployment.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '4893d01f-2b70-47e3-ab7e-bdcdb1381c3c'
    Author            = 'VCF Automation platform engineering'
    Description       = 'Requests a VCF Automation 9.1 catalog item and polls the resulting deployment request to a terminal state.'
    PowerShellVersion = '7.4'
    FunctionsToExport = @('New-VcfAutomationDeployment')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
    PrivateData       = @{
        PSData = @{
            Tags = @('VCF', 'VCF-Automation', 'REST')
        }
        VcfAutomation = @{
            Contract = 'docs/contract.json'
            Sources  = 'docs/official_sources.json'
            # VCF Automation has no specification in vmware/vcf-api-specs and no
            # generated VCF Automation binding, so this module speaks the
            # reference-derived contract directly without vendored dependencies.
            GeneratedBindingAvailable = $false
        }
    }
}
