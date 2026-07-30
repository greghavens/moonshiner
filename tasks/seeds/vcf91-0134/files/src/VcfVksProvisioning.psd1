@{
    RootModule = 'VcfVksProvisioning.psm1'
    ModuleVersion = '1.0.0'
    GUID = '277e6c94-d192-4ee8-a067-59020f668a6f'
    Author = 'VCF Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) VCF Platform Engineering'
    Description = 'Prechecked VKS cluster provisioning for VCF 9.1 Supervisors.'
    PowerShellVersion = '7.2'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            ModuleVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('New-VcfVksCluster')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'VKS', 'Supervisor')
        }
    }
}
