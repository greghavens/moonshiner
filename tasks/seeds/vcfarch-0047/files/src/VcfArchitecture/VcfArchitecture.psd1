@{
    RootModule        = 'VcfArchitecture.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '6d9dcf7d-f201-4bed-a659-b543f71ba076'
    Author            = 'Cloud Architecture Team'
    Description       = 'Generates a VCF 9.0 architecture and invokes VCF Installer validation.'
    PowerShellVersion = '7.2'
    RequiredModules   = @('VMware.Sdk.Vcf.Installer')
    FunctionsToExport = @(
        'New-VcfArchitecture',
        'Invoke-VcfArchitectureInstallerValidation'
    )
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
