@{
    RootModule = 'VcfVksEnsure.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'db67e92a-55d5-4431-bfbd-26a69b8e0132'
    Author = 'Platform Engineering'
    Description = 'Idempotently ensures a Supervisor namespace and VKS Cluster resource.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Ensure-VcfVksCluster')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
