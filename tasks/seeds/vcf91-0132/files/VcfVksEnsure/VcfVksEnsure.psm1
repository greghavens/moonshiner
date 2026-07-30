Set-StrictMode -Version Latest

function Ensure-VcfVksCluster {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $VCenterBaseUri,

        [Parameter(Mandatory)]
        [uri] $KubernetesBaseUri,

        [Parameter(Mandatory)]
        [string] $VCenterSessionId,

        [Parameter(Mandatory)]
        [string] $KubernetesBearerToken,

        [Parameter(Mandatory)]
        [string] $Supervisor,

        [Parameter(Mandatory)]
        [string] $Namespace,

        [Parameter(Mandatory)]
        [string] $ClusterName,

        [Parameter(Mandatory)]
        [string] $KubernetesVersion,

        [Parameter(Mandatory)]
        [string] $ClusterClass,

        [string] $NamespaceDescription,

        [string] $StoragePolicy,

        [string] $ServiceCidr,

        [string] $PodCidr,

        [ValidateRange(1, 10)]
        [int] $AmbiguityProbeCount = 3,

        [ValidateRange(0, 5000)]
        [int] $AmbiguityProbeDelayMilliseconds = 100
    )

    throw 'TODO: safely ensure the Supervisor namespace and VKS Cluster resource.'
}

Export-ModuleMember -Function Ensure-VcfVksCluster
