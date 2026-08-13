@{
    RootModule = 'VcfInstaller.Resilient.psm1'
    ModuleVersion = '1.0.0'
    GUID = '93bb934a-e779-4ec4-a49a-b6fb78bb9554'
    Author = 'VCF Automation Team'
    Description = 'Resilient VCF Installer 9.0 bundle-download orchestration.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Installer'
            RequiredVersion = '13.4.0.24798382'
        }
    )
    FunctionsToExport = @('Start-VcfInstallerBundleDownload')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'VCFInstaller', 'PowerCLI')
        }
    }
}
