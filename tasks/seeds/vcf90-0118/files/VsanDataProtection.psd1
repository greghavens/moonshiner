@{
    RootModule = 'VsanDataProtection.psm1'
    ModuleVersion = '1.0.0'
    GUID = '4e0e8019-e15f-478f-9d42-1fb04f2bc1c8'
    Author = 'VCF Automation Team'
    Description = 'vSAN Data Protection runbook commands for VCF 9.0.'
    PowerShellVersion = '7.2'
    RequiredModules = @('VMware.Sdk.Vcf.SddcManager')
    FunctionsToExport = @('Invoke-VsanDpSnapshotRun')
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
