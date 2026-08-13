@{
    RootModule = 'VcfInstaller.Deployment.psm1'
    ModuleVersion = '1.0.0'
    GUID = '07f61f20-daf0-4e2b-9d64-a629a37a29b2'
    Author = 'Moonshiner'
    CompanyName = 'Moonshiner'
    Copyright = '(c) 2026'
    Description = 'Precheck-gated VCF Installer deployment integration.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Installer'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Start-VcfInstallerValidatedSddcDeployment')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
