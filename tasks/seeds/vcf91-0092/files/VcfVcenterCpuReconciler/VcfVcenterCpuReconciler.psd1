@{
    RootModule = 'VcfVcenterCpuReconciler.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'd2513efd-5126-4451-b7f8-5fe9ac7ea451'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Retry-safe vCenter VM CPU reconciliation for VCF 9.1.'
    PowerShellVersion = '7.4'
    RequiredModules = @()
    FunctionsToExport = @(
        'New-VcfVcenterCpuClient'
        'Set-VcfVmCpuCount'
    )
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'vCenter', 'PowerCLI', 'Automation')
        }
    }
}
