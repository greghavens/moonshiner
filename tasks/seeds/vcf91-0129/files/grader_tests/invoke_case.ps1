[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $ModulePath,

    [Parameter(Mandatory)]
    [string] $BaseUri,

    [Parameter(Mandatory)]
    [string] $CaseConfig
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

try {
    Import-Module $ModulePath -Force
    $config = Get-Content -LiteralPath $CaseConfig -Raw | ConvertFrom-Json -AsHashtable
    $arguments = @{
        VCenterBaseUri = "$BaseUri/api"
        KubernetesBaseUri = $BaseUri
        VCenterSessionId = 'vc-session-token'
        KubernetesBearerToken = 'k8s-bearer-token'
        Supervisor = $config.supervisor
        Namespace = $config.namespace
        StoragePolicy = $config.storage_policy
        ClusterName = $config.cluster_name
        KubernetesVersion = $config.kubernetes_version
        ClusterClass = $config.cluster_class
        VmClass = $config.vm_class
        ControlPlaneReplicas = $config.control_plane_replicas
        WorkerReplicas = $config.worker_replicas
        WorkerClass = $config.worker_class
        WorkerName = $config.worker_name
        PollIntervalMilliseconds = 1
        TimeoutSeconds = 10
    }

    if ($config.ContainsKey('description')) {
        $arguments.NamespaceDescription = $config.description
    }
    if ($config.ContainsKey('storage_limit_mib')) {
        $arguments.NamespaceStorageLimitMiB = [long] $config.storage_limit_mib
    }
    if ($config.ContainsKey('service_cidr')) {
        $arguments.ServiceCidr = $config.service_cidr
    }
    if ($config.ContainsKey('pod_cidr')) {
        $arguments.PodCidr = $config.pod_cidr
    }

    $result = Invoke-VksClusterDeployment @arguments
    "CASE_RESULT=$($result | ConvertTo-Json -Depth 20 -Compress)"
}
catch {
    [Console]::Error.WriteLine("CASE_ERROR=$($_.Exception.Message)")
    exit 7
}
