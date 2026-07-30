Set-StrictMode -Version Latest

function Invoke-VksClusterDeployment {
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
        [string] $StoragePolicy,

        [Parameter(Mandatory)]
        [string] $ClusterName,

        [Parameter(Mandatory)]
        [string] $KubernetesVersion,

        [Parameter(Mandatory)]
        [string] $ClusterClass,

        [Parameter(Mandatory)]
        [string] $VmClass,

        [ValidateRange(1, 99)]
        [int] $ControlPlaneReplicas = 3,

        [ValidateRange(0, 999)]
        [int] $WorkerReplicas = 1,

        [string] $WorkerClass = 'node-pool',

        [string] $WorkerName = 'node-pool-1',

        [AllowEmptyString()]
        [string] $NamespaceDescription,

        [Nullable[long]] $NamespaceStorageLimitMiB,

        [string] $ServiceCidr,

        [string] $PodCidr,

        [ValidateRange(0, 60000)]
        [int] $PollIntervalMilliseconds = 250,

        [ValidateRange(1, 3600)]
        [int] $TimeoutSeconds = 300
    )

    throw 'Invoke-VksClusterDeployment has not been implemented.'
}

Export-ModuleMember -Function Invoke-VksClusterDeployment
