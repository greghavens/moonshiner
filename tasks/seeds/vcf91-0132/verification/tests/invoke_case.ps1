[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $ModulePath,

    [Parameter(Mandatory)]
    [string] $BaseUri,

    [Parameter(Mandatory)]
    [string] $ConfigPath
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

try {
    Import-Module $ModulePath -Force
    $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json -AsHashtable
    $arguments = @{
        VCenterBaseUri = "$BaseUri/api"
        KubernetesBaseUri = $BaseUri
        VCenterSessionId = $config.vcenter_session_id
        KubernetesBearerToken = $config.kubernetes_bearer_token
        Supervisor = $config.supervisor
        Namespace = $config.namespace
        ClusterName = $config.cluster_name
        KubernetesVersion = $config.kubernetes_version
        ClusterClass = $config.cluster_class
        TopologyVariables = $config.topology_variables
        WorkerClass = $config.worker_class
        WorkerName = $config.worker_name
        ControlPlaneReplicas = $config.control_plane_replicas
        WorkerReplicas = $config.worker_replicas
        AmbiguityProbeCount = 3
        AmbiguityProbeDelayMilliseconds = 1
    }

    $liveGapError = $null
    try {
        $null = Ensure-VcfVksCluster @arguments
    }
    catch {
        $liveGapError = $_
    }
    if ($null -eq $liveGapError) {
        throw 'The exact live no-Supervisor case unexpectedly succeeded.'
    }

    $first = Ensure-VcfVksCluster @arguments
    $second = Ensure-VcfVksCluster @arguments

    "FIRST_RESULT=$($first | ConvertTo-Json -Depth 20 -Compress)"
    "SECOND_RESULT=$($second | ConvertTo-Json -Depth 20 -Compress)"
}
catch {
    [Console]::Error.WriteLine("CASE_ERROR=$($_.Exception.Message)")
    exit 7
}
