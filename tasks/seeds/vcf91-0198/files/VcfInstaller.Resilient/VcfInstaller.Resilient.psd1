@{
    RootModule = 'VcfInstaller.Resilient.psm1'
    ModuleVersion = '1.0.0'
    GUID = '675a4993-cb6f-438b-8a26-eae8caf58198'
    Author = 'VCF Platform Automation'
    Description = 'Resilient VCF Installer deployment workflow with access-token refresh.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Installer'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Invoke-VcfInstallerResilientDeployment')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
