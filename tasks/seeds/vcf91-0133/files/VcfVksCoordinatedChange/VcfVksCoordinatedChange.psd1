@{
    RootModule = 'VcfVksCoordinatedChange.psm1'
    ModuleVersion = '1.0.0'
    GUID = '0f23dfd7-7458-4194-a857-b727630cd01b'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'A partial-success-aware Supervisor and VKS coordinated change.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Invoke-VcfVksCoordinatedChange')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'VKS', 'Supervisor', 'PowerCLI', 'ClusterAPI')
        }
    }
}
