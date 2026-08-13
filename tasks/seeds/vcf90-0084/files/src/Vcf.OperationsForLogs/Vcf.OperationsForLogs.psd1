@{
    RootModule = 'Vcf.OperationsForLogs.psm1'
    ModuleVersion = '1.0.0'
    GUID = '6dad9a67-92fe-4d28-a049-e9ac0ce1a169'
    Author = 'VCF Automation Team'
    Description = 'PowerShell integration for contract-pinned VCF Operations for Logs operations.'
    PowerShellVersion = '7.2'
    RequiredModules = @(
        @{ ModuleName = 'VMware.Sdk.Vcf.Ops'; ModuleVersion = '13.4.0.24798382' }
    )
    FunctionsToExport = @('Set-VcfOpsLogNotificationWebhook')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'OperationsForLogs', 'PowerCLI')
        }
    }
}
