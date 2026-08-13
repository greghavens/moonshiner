@{
    RootModule        = 'VCFOpsNetworks.psm1'
    ModuleVersion     = '0.1.0'
    GUID              = '6d1f0c94-0287-4a3b-9f5e-2c7b18ae4d10'
    Author            = 'Platform Networking'
    CompanyName       = 'Contoso'
    Description       = 'Application rollout helpers for VCF Operations for Networks 9.1.'
    PowerShellVersion = '7.2'

    FunctionsToExport = @(
        'Connect-VCFOpsNetworksServer',
        'New-VCFOpsNetworksApplication',
        'Disconnect-VCFOpsNetworksServer'
    )
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
