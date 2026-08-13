@{
    RootModule = 'Vcf.Nsx.Policy.Async.psm1'
    ModuleVersion = '0.1.0'
    GUID = 'e7cf9f2f-fc45-4b07-9a96-9f499db27c62'
    Author = 'VCF Integration Team'
    CompanyName = 'Community'
    Copyright = '(c) VCF Integration Team. Apache-2.0.'
    Description = 'Submit an NSX Policy segment and wait for realization.'
    PowerShellVersion = '7.2'
    FunctionsToExport = @(
        'New-VcfNsxPowerCliTransport',
        'Set-VcfNsxInfraSegment'
    )
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'NSX', 'Policy', 'PowerCLI')
        }
    }
}
