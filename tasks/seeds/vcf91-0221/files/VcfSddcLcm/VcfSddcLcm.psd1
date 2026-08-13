@{
    RootModule = 'VcfSddcLcm.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'ec48006e-cde6-4ae4-b1bf-5587fd514f0e'
    Author = 'VCF automation team'
    CompanyName = 'Community'
    Copyright = '(c) 2026. Apache-2.0 derived contract attribution is in docs/official_sources.json.'
    Description = 'Contract-pinned VCF 9.1 SDDC and Fleet lifecycle configuration helper.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.SddcManager'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @('Set-VcfSddcLcmConfiguration')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('VCF', 'SDDC', 'Fleet', 'Lifecycle')
            ExternalModuleDependencies = @('VMware.Sdk.Vcf.SddcManager')
        }
    }
}
