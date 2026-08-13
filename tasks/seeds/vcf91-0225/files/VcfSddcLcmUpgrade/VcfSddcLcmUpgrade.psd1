@{
    RootModule        = 'VcfSddcLcmUpgrade.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = '18b84e63-3ce0-4cb5-94a9-154b3ee7aed0'
    Author            = 'VCF Lifecycle Engineering'
    CompanyName       = 'VCF Lifecycle Engineering'
    Description       = 'Drives a multi-step VMware Cloud Foundation 9.1 SDDC LCM component upgrade run over a caller-owned PowerCLI session and reports the outcome of every step.'
    PowerShellVersion = '7.4'
    FunctionsToExport = @('Invoke-VcfSddcLcmComponentUpgrade')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
    PrivateData       = @{
        PSData = @{
            Tags = @('VCF', 'SDDC-LCM', 'Lifecycle', 'PowerCLI')
        }
        # The environment installs these; the module never vendors them.
        RequiredPowerCliModules = @(
            @{ ModuleName = 'VMware.Sdk.Vcf.Installer'; RequiredVersion = '13.5.0.25380678' }
        )
        ContractSource = 'docs/contract.json'
    }
}
