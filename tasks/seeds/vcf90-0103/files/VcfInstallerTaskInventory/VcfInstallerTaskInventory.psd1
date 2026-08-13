@{
    RootModule = 'VcfInstallerTaskInventory.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'bc06530d-77e3-4db6-9c4d-0cfb974300b4'
    Author = 'VCF Automation Team'
    Description = 'Stable, complete VCF Installer task inventory.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Installer'
            RequiredVersion = '13.4.0.24798382'
        }
    )
    FunctionsToExport = @('Get-VcfInstallerTaskInventory')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
