@{
    RootModule = 'VcfInstaller.Proxy.psm1'
    ModuleVersion = '1.0.0'
    GUID = '6757bc90-1ff4-4f2e-abd4-0c8ca46df252'
    Author = 'Moonshiner fixture'
    Description = 'Wait-aware integration for the VCF Installer proxy configuration API.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Installer'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Set-VcfInstallerProxyConfiguration')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
