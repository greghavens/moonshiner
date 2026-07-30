Set-StrictMode -Version Latest

function Invoke-VcfVksCoordinatedChange {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [VMware.Bindings.vSphere.Api.IVcenterNamespacesInstancesApi]
        $NamespaceApi,

        [Parameter(Mandatory)]
        [string] $KubernetesBaseUri,

        [Parameter(Mandatory)]
        [string] $KubernetesToken,

        [Parameter(Mandatory)]
        [string] $Supervisor,

        [Parameter(Mandatory)]
        [string] $Namespace,

        [Parameter(Mandatory)]
        [string] $ClusterName,

        [Parameter(Mandatory)]
        [string] $ClusterClass,

        [Parameter(Mandatory)]
        [string] $NamespaceDescription,

        [Parameter(Mandatory)]
        [string] $TargetVersion,

        [switch] $SkipCertificateCheck
    )

    throw [NotImplementedException]::new(
        'TODO: implement the coordinated Supervisor and VKS change.'
    )
}

Export-ModuleMember -Function Invoke-VcfVksCoordinatedChange
