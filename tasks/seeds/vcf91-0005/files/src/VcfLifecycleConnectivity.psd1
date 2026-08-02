@{
    RootModule = 'VcfLifecycleConnectivity.psm1'
    ModuleVersion = '1.0.0'
    GUID = '58eb2605-fc93-4efe-80cf-82c1803dd8f5'
    Author = 'VCF Automation Team'
    Description = 'Coordinates lifecycle connectivity changes in VCF 9.1 SDDC Manager.'
    PowerShellVersion = '7.2'
    RequiredModules = @('VMware.Sdk.Vcf.SddcManager')
    FunctionsToExport = @('Invoke-VcfLifecycleConnectivityChange')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'SDDCManager', 'PowerCLI')
        }
    }
}
