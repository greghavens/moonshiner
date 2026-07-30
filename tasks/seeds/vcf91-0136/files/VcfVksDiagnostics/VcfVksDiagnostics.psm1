Set-StrictMode -Version Latest

function Get-VcfVksFailureDiagnosis {
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
        [string] $KubernetesToken,

        [Parameter(Mandatory)]
        [string] $ControllerNamespace,

        [ValidateSet('http', 'https')]
        [string] $KubernetesScheme = 'https',

        [switch] $SkipCertificateCheck
    )

    throw [NotImplementedException]::new(
        'TODO: correlate the VKS Cluster event and controller log evidence.'
    )
}

Export-ModuleMember -Function Get-VcfVksFailureDiagnosis
