Set-StrictMode -Version Latest

<#
    vSphere Automation API for vCenter - virtual machine cloning (VCF 9.0).

    The three service operations this module drives are projected in
    docs/contract.json from the pinned OpenAPI specification recorded in
    docs/official_sources.json:

      Cis.Session_create
      Vcenter.VM_clone$Task
      Cis.Tasks_get

    Requests are issued with the OpenAPI client runtime that ships with the
    VMware.Sdk.Vcf PowerCLI modules, for example:

      $http   = [System.Net.Http.HttpClient]::new()
      $client = [VMware.Binding.OpenApi.Client.ApiClient]::new($http, $basePath, $handler)
      $config = [VMware.Binding.OpenApi.Client.Configuration]::new()
      $config.BasePath = $basePath
      $options = [VMware.Binding.OpenApi.Client.RequestOptions]::new()
      $options.PathParameters.Add('task', $taskId)
      $options.QueryParameters.Add('vmw-task', 'true')
      $options.HeaderParameters.Add('vmware-api-session-id', $token)
      $options.Data = @{ name = $Name; source = $SourceVm }
      $response = $client.Post[string]('/vcenter/vm', $options, $config)

    $response exposes StatusCode, Data and ErrorText. Non-success statuses are
    returned rather than thrown, so callers must inspect StatusCode themselves.
#>

function Connect-VcfVcenterApi {
    <#
    .SYNOPSIS
        Creates a vCenter session and returns a connection object.
    .DESCRIPTION
        Calls Cis.Session_create with HTTP Basic credentials and captures the
        returned session token.
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

    throw [System.NotImplementedException]::new('Connect-VcfVcenterApi is not implemented.')
}

function New-VcfVcenterVmClone {
    <#
    .SYNOPSIS
        Clones a virtual machine and follows the resulting task to a terminal
        state.
    .DESCRIPTION
        Calls Vcenter.VM_clone$Task and then polls Cis.Tasks_get until the task
        reaches a terminal status.
    #>
    [CmdletBinding()]
    [OutputType([psobject])]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [psobject] $Connection,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $SourceVm,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Name,

        [Parameter()]
        [ValidateNotNullOrEmpty()]
        [string] $Folder,

        [Parameter()]
        [ValidateNotNullOrEmpty()]
        [string] $ResourcePool,

        [Parameter()]
        [ValidateNotNullOrEmpty()]
        [string] $HostSystem,

        [Parameter()]
        [ValidateNotNullOrEmpty()]
        [string] $Cluster,

        [Parameter()]
        [ValidateNotNullOrEmpty()]
        [string] $Datastore,

        [Parameter()]
        [switch] $PowerOn,

        [Parameter()]
        [ValidateNotNull()]
        [string[]] $DisksToRemove,

        [Parameter()]
        [ValidateNotNullOrEmpty()]
        [string] $GuestCustomizationName,

        [Parameter()]
        [ValidateRange(0, 3600)]
        [int] $PollIntervalSeconds = 1,

        [Parameter()]
        [ValidateRange(1, 86400)]
        [int] $TimeoutSeconds = 120
    )

    throw [System.NotImplementedException]::new('New-VcfVcenterVmClone is not implemented.')
}

Export-ModuleMember -Function 'Connect-VcfVcenterApi', 'New-VcfVcenterVmClone'
