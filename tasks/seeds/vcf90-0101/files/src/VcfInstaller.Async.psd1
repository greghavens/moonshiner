@{
    RootModule = 'VcfInstaller.Async.psm1'
    ModuleVersion = '1.0.0'
    GUID = '56ab3e1b-268a-4cb9-954d-7ee556bb0bf4'
    Author = 'VCF Automation Team'
    Description = 'Asynchronous helpers for the VMware Cloud Foundation Installer API.'
    PowerShellVersion = '7.2'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Installer'
            ModuleVersion = '13.4.0.24798382'
        }
    )
    FunctionsToExport = @('Start-VcfInstallerBundleDownload')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
