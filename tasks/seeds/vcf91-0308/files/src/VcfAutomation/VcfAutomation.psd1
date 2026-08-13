@{
    RootModule        = 'VcfAutomation.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = 'd6b1f0c2-4f3a-4b7e-9a51-6c2f0b8e7a34'
    Author            = 'VCF Automation tooling'
    Description       = 'Day-2 deployment action sweep for VCF Automation in VMware Cloud Foundation 9.1. The module uses the documented REST operations directly.'
    PowerShellVersion = '7.2'

    FunctionsToExport = @('Invoke-VcfaDeploymentActionSweep')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()

    PrivateData       = @{
        PSData = @{
            Tags = @('VCF', 'VCF-Automation', 'VMware', 'REST')
        }
    }
}
