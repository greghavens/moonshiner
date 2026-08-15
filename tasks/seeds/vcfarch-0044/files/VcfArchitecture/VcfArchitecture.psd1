@{
    RootModule = 'VcfArchitecture.psm1'
    ModuleVersion = '1.0.0'
    GUID = '04367389-c8e2-47f7-a8da-e134dce9edda'
    Author = 'Northstar Platform Engineering'
    Description = 'Produces a machine-checkable VCF greenfield architecture and existing-estate migration plan.'
    PowerShellVersion = '7.2'
    RequiredModules = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.Installer'; RequiredVersion = '13.4.0.24798382' }
    )
    FunctionsToExport = @('New-VcfArchitecture')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
