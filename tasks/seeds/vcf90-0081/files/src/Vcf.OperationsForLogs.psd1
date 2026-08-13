@{
    RootModule = 'Vcf.OperationsForLogs.psm1'
    ModuleVersion = '1.0.0'
    GUID = '785b38da-a31b-4cf4-bfd0-ad6a09a0cb73'
    Author = 'VCF Automation Team'
    Description = 'VCF Operations for Logs 9.0 upgrade integration for PowerCLI.'
    PowerShellVersion = '7.2'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Ops'
            RequiredVersion = '13.4.0.24798382'
        }
    )
    FunctionsToExport = @('Invoke-VcfLogsUpgrade')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'PowerCLI', 'OperationsForLogs')
        }
    }
}
