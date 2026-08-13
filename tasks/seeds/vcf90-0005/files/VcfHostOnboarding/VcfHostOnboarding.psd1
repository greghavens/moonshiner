@{
    RootModule        = 'VcfHostOnboarding.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = 'b7d4f0c5-1e2a-4a86-9c3f-4f5a2d0e91b6'
    Author            = 'Moonshiner'
    Description       = 'Runs the VCF 9.0 network pool and host commission onboarding change and reports every step of it.'
    PowerShellVersion = '7.4'
    RequiredModules   = @(
        @{
            ModuleName      = 'VMware.Sdk.Vcf.SddcManager'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Invoke-VcfHostOnboarding')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
    PrivateData       = @{
        PSData = @{
            Tags = @('VCF', 'SDDCManager', 'Onboarding')
        }
    }
}
