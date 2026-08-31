@{
    RootModule = 'VcfVcenterCredentialGate.psm1'
    ModuleVersion = '1.0.0'
    GUID = '83538272-4ad7-42bd-8242-f94ed4ddd4d9'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Drain-safe vCenter API session credential cutover for VCF 9.1.'
    PowerShellVersion = '7.4'
    RequiredModules = @()
    FunctionsToExport = @(
        'New-VcfVcenterCredentialClient'
        'Get-VcfVcenterAuthorizationRole'
        'Set-VcfVcenterCredential'
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
