Set-StrictMode -Version Latest

<#
    vSAN Data Protection - on-demand protection group snapshots (VCF 9.1).

    The three service operations this module drives are projected in
    docs/contract.json from the pinned OpenAPI specification recorded in
    docs/official_sources.json:

      Snapservice.Sessions_create
      Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task
      Snapservice.Tasks_get

    Requests are issued with the OpenAPI client runtime that ships with the
    VMware.Sdk.Vcf PowerCLI modules, for example:

      $http   = [System.Net.Http.HttpClient]::new()
      $client = [VMware.Binding.OpenApi.Client.ApiClient]::new($http, $basePath, $handler)
      $config = [VMware.Binding.OpenApi.Client.Configuration]::new()
      $config.BasePath = $basePath
      $options = [VMware.Binding.OpenApi.Client.RequestOptions]::new()
      $options.PathParameters.Add('cluster', $Cluster)
      $options.QueryParameters.Add('vmw-task', 'true')
      $options.HeaderParameters.Add('vmware-api-session-id', $token)
      $options.Data = @{ name = $Name }
      $response = $client.Post[string]('/snapservice/clusters/{cluster}/...', $options, $config)

    $response exposes StatusCode, Data and ErrorText. Non-success statuses are
    returned rather than thrown, so callers must inspect StatusCode themselves.
#>

function Connect-VsanDpAppliance {
    <#
    .SYNOPSIS
        Creates a snapservice session and returns a connection object.
    .DESCRIPTION
        Calls Snapservice.Sessions_create with HTTP Basic credentials and
        captures the returned session token.
    #>
    [CmdletBinding()]
    [OutputType([psobject])]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Server,

        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [pscredential] $Credential,

        [Parameter()]
        [switch] $SkipCertificateCheck
    )

    throw [System.NotImplementedException]::new('Connect-VsanDpAppliance is not implemented.')
}

function New-VsanDpProtectionGroupSnapshot {
    <#
    .SYNOPSIS
        Takes an on-demand protection group snapshot and follows the resulting
        task to a terminal state.
    .DESCRIPTION
        Calls Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task and
        then polls Snapservice.Tasks_get until the task reaches a terminal
        status.
    #>
    [CmdletBinding()]
    [OutputType([psobject])]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [psobject] $Connection,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Cluster,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $ProtectionGroup,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Name,

        [Parameter()]
        [long] $RetentionDuration,

        [Parameter()]
        [ValidateSet('MINUTE', 'HOUR', 'DAY', 'WEEK', 'MONTH', 'YEAR')]
        [string] $RetentionUnit,

        [Parameter()]
        [ValidateRange(0, 3600)]
        [int] $PollIntervalSeconds = 1,

        [Parameter()]
        [ValidateRange(1, 86400)]
        [int] $TimeoutSeconds = 120
    )

    throw [System.NotImplementedException]::new('New-VsanDpProtectionGroupSnapshot is not implemented.')
}

Export-ModuleMember -Function 'Connect-VsanDpAppliance', 'New-VsanDpProtectionGroupSnapshot'
