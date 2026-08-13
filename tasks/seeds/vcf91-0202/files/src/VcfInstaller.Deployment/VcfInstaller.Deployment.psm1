Set-StrictMode -Version Latest

function Start-VcfInstallerValidatedSddcDeployment {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [object] $Server,

        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [VMware.Bindings.Vcf.Installer.Model.SddcSpec] $SddcSpec
    )

    throw 'Start-VcfInstallerValidatedSddcDeployment has not been implemented.'
}

Export-ModuleMember -Function Start-VcfInstallerValidatedSddcDeployment
