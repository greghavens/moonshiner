@{
    RootModule = 'VsanDataProtection.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'b0296a7a-bc55-42b5-891d-3c42f79b12b1'
    Author = 'Platform Automation'
    Description = 'Focused VMware Cloud Foundation 9.0 vSAN Data Protection integration.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            RequiredVersion = '13.4.0.24798382'
        }
    )
    FunctionsToExport = @('New-VsanProtectionGroupSnapshot')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
