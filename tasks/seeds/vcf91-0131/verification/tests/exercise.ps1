param(
    [Parameter(Mandatory)]
    [int] $Port,

    [Parameter(Mandatory)]
    [string] $SessionToken,

    [Parameter(Mandatory)]
    [string] $KubernetesToken,

    [Parameter(Mandatory)]
    [string] $Namespace,

    [Parameter(Mandatory)]
    [int] $PageSize,

    [Parameter(Mandatory)]
    [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$FilesRoot = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path (
    Join-Path $FilesRoot 'VcfVksClusterInventory'
) 'VcfVksClusterInventory.psd1'

Import-Module VMware.Sdk.Vcf.SddcManager -ErrorAction Stop
Import-Module $ManifestPath -Force -ErrorAction Stop

$Handler = [Net.Http.HttpClientHandler]::new()
$HttpClient = [Net.Http.HttpClient]::new($Handler, $false)
$Api = $null
try {
    if (
        -not $HttpClient.DefaultRequestHeaders.TryAddWithoutValidation(
            'vmware-api-session-id',
            $SessionToken
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

    $First = @(
        Get-VcfVksClusterInventory `
            -NamespaceApi $Api `
            -Namespace $Namespace `
            -KubernetesToken $KubernetesToken `
            -PageSize $PageSize `
            -KubernetesScheme http
    )
    $Second = @(
        Get-VcfVksClusterInventory `
            -NamespaceApi $Api `
            -Namespace $Namespace `
            -KubernetesToken $KubernetesToken `
            -PageSize $PageSize `
            -KubernetesScheme http
    )

    [ordered]@{
        first = $First
        second = $Second
    } |
        ConvertTo-Json -Depth 100 -Compress |
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
