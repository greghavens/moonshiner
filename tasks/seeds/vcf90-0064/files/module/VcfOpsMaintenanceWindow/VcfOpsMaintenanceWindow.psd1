@{
    RootModule        = 'VcfOpsMaintenanceWindow.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = 'f4a1c0d2-5b67-4e83-9c1a-7d24b6e05f38'
    Author            = 'Platform Automation'
    Description       = 'Reconciles a VMware Cloud Foundation Operations maintenance schedule so that repeated runs converge on one schedule.'
    PowerShellVersion = '7.2'
    RequiredModules   = @('VMware.Sdk.Vcf.Ops')
    FunctionsToExport = @('Set-VcfOpsMaintenanceSchedule')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
