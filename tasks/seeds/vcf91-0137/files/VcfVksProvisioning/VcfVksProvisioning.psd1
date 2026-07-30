@{
    RootModule = 'VcfVksProvisioning.psm1'
    ModuleVersion = '1.0.0'
    GUID = '2965bb8c-3aee-40e7-b9c0-eec2b72c3e19'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Contract-driven VKS Cluster API provisioning and polling.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('New-VcfVksClusterAndWait')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
