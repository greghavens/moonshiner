@{
    RootModule = 'src/VksSupervisor.psm1'
    ModuleVersion = '1.0.0'
    GUID = '88e29d17-c5a7-4b65-b66a-2f2740b71d4f'
    Author = 'Moonshiner'
    CompanyName = 'Independent'
    Copyright = 'Copyright (c) 2026'
    Description = 'Contract-driven Supervisor namespace and VKS Cluster API workflow.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0'
        }
    )
    FunctionsToExport = @('Invoke-VksClusterDeployment')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'VKS', 'Supervisor', 'ClusterAPI')
        }
    }
}
