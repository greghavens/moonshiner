@{
    RootModule = 'VcfOpsLogs.psm1'
    ModuleVersion = '1.0.0'
    GUID = '9df2bd27-e377-4128-acd1-770978547e62'
    Author = 'Platform Automation'
    Description = 'Contract-pinned VCF Operations for Logs 9.0 rollout helpers.'
    PowerShellVersion = '7.2'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Ops'
            ModuleVersion = '13.4.0.24798382'
        }
    )
    FunctionsToExport = @('Invoke-VcfOpsLogsForwarderRollout')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'OperationsForLogs', 'PowerCLI')
        }
    }
}
