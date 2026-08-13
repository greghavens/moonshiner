@{
    RootModule        = 'VcfOpsCredentialRotation.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = 'b3f6a1d2-7c48-4e05-9a13-5d2e8c4f7b60'
    Author            = 'Platform Engineering'
    Description       = 'Drain-safe credential rotation for VCF Operations adapter instances.'
    PowerShellVersion = '7.2'
    FunctionsToExport = @('Invoke-VcfOpsCredentialRotation')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
