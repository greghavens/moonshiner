@{
    RootModule = 'VcfOpsLogCredential.psm1'
    ModuleVersion = '1.0.0'
    GUID = '6fe7812c-69a9-44bd-a210-f2c8c160ae2b'
    Author = 'Platform Engineering'
    CompanyName = 'Example'
    Copyright = '(c) Platform Engineering'
    Description = 'Lease-safe VCF Operations for Logs credential rotation.'
    PowerShellVersion = '7.4'
    RequiredModules = @(
        @{
            ModuleName = 'VMware.Sdk.Vcf.Ops'
            RequiredVersion = '13.5.0.25380678'
        }
    )
    FunctionsToExport = @(
        'New-VcfOpsLogCredentialGate'
        'Get-VcfOpsLogCredentialLease'
        'Invoke-VcfOpsLogCredentialRotation'
    )
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
