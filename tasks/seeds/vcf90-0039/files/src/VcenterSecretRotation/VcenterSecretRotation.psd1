@{
    RootModule        = 'VcenterSecretRotation.psm1'
    ModuleVersion     = '0.1.0'
    GUID              = 'b0c9c2a4-7f31-4a0e-9b4d-6d0f2b1a5e77'
    Author            = 'Platform Automation'
    Description       = 'Rotates the vCenter automation service-account secret over the vSphere Automation API without stranding in-flight requests that are still bound to the retiring session.'
    PowerShellVersion = '7.2'
    FunctionsToExport = @('Invoke-VcenterCredentialRotation')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
    PrivateData       = @{
        PSData = @{
            Tags = @('VCF', 'vCenter', 'vSphere-Automation-API', 'credential-rotation')
        }
    }
}
