Set-StrictMode -Version Latest

function New-VcfVksClusterAndWait {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [VMware.Bindings.vSphere.Api.IVcenterNamespacesUserInstancesApi]
        $NamespaceApi,

        [Parameter(Mandatory)]
        [string] $Namespace,

        [Parameter(Mandatory)]
        [string] $ClusterName,

        [Parameter(Mandatory)]
        [string] $ClusterClass,

        [Parameter(Mandatory)]
        [string] $KubernetesVersion,

        [Parameter(Mandatory)]
        [string] $KubernetesToken,

        [ValidateRange(1, 100)]
        [int] $MaxPolls = 20,

        [ValidateRange(0, 60000)]
        [int] $PollIntervalMilliseconds = 1000,

        [ValidateSet('http', 'https')]
        [string] $KubernetesScheme = 'https',

        [switch] $SkipCertificateCheck
    )

    throw [NotImplementedException]::new(
        'TODO: create the VKS Cluster and poll it to a terminal phase.'
    )
}

Export-ModuleMember -Function New-VcfVksClusterAndWait
