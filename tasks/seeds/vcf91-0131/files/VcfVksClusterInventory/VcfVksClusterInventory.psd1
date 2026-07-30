@{
    RootModule = 'VcfVksClusterInventory.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'd2f60446-6ae5-42a9-9b0e-bf17f663ae27'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Complete and stable VKS Cluster API inventory for VCF 9.1.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Get-VcfVksClusterInventory')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'VKS', 'Supervisor', 'PowerCLI', 'ClusterAPI')
        }
    }
}
