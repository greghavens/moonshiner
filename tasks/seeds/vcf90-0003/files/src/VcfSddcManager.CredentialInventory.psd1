@{
    RootModule = 'VcfSddcManager.CredentialInventory.psm1'
    ModuleVersion = '1.0.0'
    GUID = '462bbb20-83f4-5b74-981c-38ecafe2cd6a'
    Author = 'Moonshiner fixture'
    Description = 'Complete, stably ordered credential inventory for the VMware Cloud Foundation 9.0 SDDC Manager API.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Get-VcfSddcManagerCredentialInventory')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
