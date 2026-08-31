[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [int] $Port,

    [Parameter(Mandatory)]
    [string] $ConfigPath,

    [Parameter(Mandatory)]
    [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$FilesRoot = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path (
    Join-Path $FilesRoot 'VcfVksProvisioning'
) 'VcfVksProvisioning.psd1'
$Config = Get-Content -LiteralPath $ConfigPath -Raw |
    ConvertFrom-Json -AsHashtable

Import-Module VMware.Sdk.Vcf.SddcManager -ErrorAction Stop
Import-Module $ManifestPath -Force -ErrorAction Stop

$Handler = [Net.Http.HttpClientHandler]::new()
$Handler.AllowAutoRedirect = $false
$HttpClient = [Net.Http.HttpClient]::new($Handler, $false)
$Api = $null
try {
    if (
        -not $HttpClient.DefaultRequestHeaders.TryAddWithoutValidation(
            'vmware-api-session-id',
            $Config.vcenter_session_id
        )
    ) {
        throw 'Could not configure the generated vSphere binding.'
    }
    $BasePath = "http://127.0.0.1:${Port}/api"
    $Api = [VMware.Bindings.vSphere.Api.VcenterNamespacesUserInstancesApi]::new(
        $HttpClient,
        $BasePath,
        $Handler
    )

    $arguments = @{
        NamespaceApi = $Api
        Namespace = $Config.namespace
        ClusterName = $Config.cluster_name
        ClusterClass = $Config.cluster_class
        KubernetesVersion = $Config.kubernetes_version
        TopologyVariables = $Config.topology_variables
        WorkerClass = $Config.worker_class
        WorkerName = $Config.worker_name
        ControlPlaneReplicas = $Config.control_plane_replicas
        WorkerReplicas = $Config.worker_replicas
        KubernetesToken = $Config.kubernetes_bearer_token
        MaxPolls = 5
        PollIntervalMilliseconds = 0
        KubernetesScheme = 'http'
    }
    $LiveGapError = $null
    try {
        $null = New-VcfVksClusterAndWait @arguments
    }
    catch {
        $LiveGapError = $_
    }
    if ($null -eq $LiveGapError) {
        throw 'The exact live blank namespace summary unexpectedly succeeded.'
    }

    $Result = New-VcfVksClusterAndWait `
        -NamespaceApi $Api `
        -Namespace $Config.namespace `
        -ClusterName $Config.cluster_name `
        -ClusterClass $Config.cluster_class `
        -KubernetesVersion $Config.kubernetes_version `
        -TopologyVariables $Config.topology_variables `
        -WorkerClass $Config.worker_class `
        -WorkerName $Config.worker_name `
        -ControlPlaneReplicas $Config.control_plane_replicas `
        -WorkerReplicas $Config.worker_replicas `
        -KubernetesToken $Config.kubernetes_bearer_token `
        -MaxPolls 5 `
        -PollIntervalMilliseconds 0 `
        -KubernetesScheme http

    $Result |
        ConvertTo-Json -Depth 30 -Compress |
        Set-Content `
            -LiteralPath $OutputPath `
            -Encoding utf8NoBOM `
            -NoNewline
}
finally {
    if ($null -ne $Api) {
        $Api.Dispose()
    }
    $HttpClient.Dispose()
    $Handler.Dispose()
}
