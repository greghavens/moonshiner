@{
    RootModule           = 'VcfAutomation.Triage.psm1'
    ModuleVersion        = '1.0.0'
    GUID                 = 'f0a4c7e2-5b93-4d18-a6c0-3e719b84d2f5'
    Author               = 'Payments Platform Team'
    CompanyName          = 'example.internal'
    Description          = 'Triage helpers for VCF Automation 9.1 deployments. Wraps the VM Apps Org - Deployment REST operations described in docs/contract.json.'
    PowerShellVersion    = '7.2'

    FunctionsToExport    = @(
        'Get-VcfAutomationDeployment'
        'Get-VcfAutomationDeploymentRequest'
        'Get-VcfAutomationRequest'
        'Get-VcfAutomationRequestEvent'
        'Get-VcfAutomationEventLog'
        'Submit-VcfAutomationDeploymentAction'
        'Invoke-VcfAutomationDeploymentTriage'
    )
    CmdletsToExport      = @()
    VariablesToExport    = @()
    AliasesToExport      = @()

    # VCF PowerCLI is a prerequisite of the wider toolchain this module ships
    # with and is installed on the operator workstation, never vendored here.
    # It is declared as an *external* dependency rather than in RequiredModules
    # so that importing this module does not hard-fail on a host where the SDK
    # has not been installed yet.
    #
    # Note that the VMware.Sdk.Vcf family ships generated bindings for
    # CloudBuilder, SddcManager, Installer and Ops only. VCF Automation has no
    # generated binding because it has no published API specification, which is
    # why the operations in this module are hand-rolled REST calls written
    # against the reference-derived contract in docs/contract.json.
    PrivateData          = @{
        PSData = @{
            Tags                       = @('VCF', 'VCF-Automation', 'VMware', 'Triage')
            ExternalModuleDependencies = @(
                'VMware.Sdk.Vcf.SddcManager'
                'VMware.Sdk.Vcf.Ops'
            )
        }
    }
}
