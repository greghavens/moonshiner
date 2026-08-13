@{
    RootModule           = 'VcfOps.MaintenanceWindow.psm1'
    ModuleVersion        = '1.0.0'
    GUID                 = '9c4d1b02-6f5e-4a7d-8c31-2b7e5f0a44d1'
    Author               = 'VCF Operations Platform Engineering'
    CompanyName          = 'Internal'
    Description          = 'Declarative maintenance window management for VMware Cloud Foundation Operations 9.1, built on the VMware.Sdk.Vcf.Ops PowerCLI module.'
    PowerShellVersion    = '7.4'
    RequiredModules      = @('VMware.Sdk.Vcf.Ops')
    FunctionsToExport    = @('Set-VcfOpsMaintenanceWindow')
    CmdletsToExport      = @()
    VariablesToExport    = @()
    AliasesToExport      = @()
    PrivateData          = @{
        PSData = @{
            Tags       = @('VCF', 'VCF-Operations', 'MaintenanceSchedule')
            ProjectUri = 'https://github.com/vmware/vcf-api-specs'
        }
        Contract = @{
            Document   = 'docs/contract.json'
            Provenance = 'docs/official_sources.json'
            Operations = @(
                'getMaintenanceSchedules',
                'createMaintenanceSchedules',
                'updateMaintenanceSchedules'
            )
        }
    }
}
