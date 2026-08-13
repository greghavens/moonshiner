#Requires -Version 7.4
Set-StrictMode -Version Latest

# ---------------------------------------------------------------------------
# Transport helpers.
#
# These are already implemented. They wrap the OpenAPI binding layer that ships
# with the VMware.Sdk.Vcf PowerCLI modules (module VMware.OpenAPI, assembly
# VMware.Binding.OpenApi), which the environment installs as a prerequisite.
#
# They deliberately do NOT add headers, query parameters or a request body.
# Everything that appears on the wire comes from the RequestOptions object you
# hand to Invoke-NiRequest, so the request shape is entirely yours to decide.
# ---------------------------------------------------------------------------

function New-NiApiConnection {
    <#
    .SYNOPSIS
        Builds a connection to the VCF Operations for Networks API.
    .OUTPUTS
        A PSCustomObject with Client ([VMware.Binding.OpenApi.Client.ApiClient])
        and Configuration ([VMware.Binding.OpenApi.Client.Configuration]).
        Configuration.BasePath already includes the /api/ni server base path
        declared by the specification, so operation paths passed to
        Invoke-NiRequest are relative to it (for example '/auth/token').
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string] $Server,

        [Parameter()]
        [switch] $SkipCertificateCheck
    )

    $basePath = ($Server.TrimEnd('/')) + '/api/ni'

    $configuration = New-Object VMware.Binding.OpenApi.Client.Configuration
    $configuration.BasePath = $basePath

    $handler = New-Object System.Net.Http.HttpClientHandler
    if ($SkipCertificateCheck) {
        $handler.ServerCertificateCustomValidationCallback = {
            param($message, $cert, $chain, $errors) $true
        }
    }

    $httpClient = New-Object System.Net.Http.HttpClient($handler, $true)
    $apiClient = New-Object VMware.Binding.OpenApi.Client.ApiClient($httpClient, $basePath, $handler)

    [pscustomobject]@{
        Client        = $apiClient
        Configuration = $configuration
        BasePath      = $basePath
    }
}

function New-NiRequestOptions {
    <#
    .SYNOPSIS
        Returns an empty [VMware.Binding.OpenApi.Client.RequestOptions].
    .DESCRIPTION
        Populate the instance before passing it to Invoke-NiRequest:

            $options.PathParameters.Add('requestId', $id)      # {requestId} in the path
            $options.QueryParameters.Add('discovery_type', $t) # ?discovery_type=...
            $options.HeaderParameters.Add('Authorization', $v) # request header
            $options.Data = [ordered]@{ key = 'value' }        # JSON request body

        Data is serialized to JSON exactly as given: an ordered dictionary
        produces exactly those keys in that order and nothing else. A key you
        never add is a key that never reaches the wire.
    #>
    [CmdletBinding()]
    param()

    New-Object VMware.Binding.OpenApi.Client.RequestOptions
}

function Invoke-NiRequest {
    <#
    .SYNOPSIS
        Issues one request through the PowerCLI OpenAPI binding layer.
    .OUTPUTS
        [VMware.Binding.OpenApi.Client.ApiResponse[System.Object]].
        Useful members: StatusCode, Data (deserialized JSON), RawContent, ErrorText.
    .NOTES
        This client does NOT raise on a non-success HTTP status. It returns the
        response with StatusCode set, so callers must inspect StatusCode
        themselves.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object] $Connection,

        [Parameter(Mandatory)]
        [ValidateSet('GET', 'POST', 'PUT', 'DELETE', 'PATCH')]
        [string] $Method,

        [Parameter(Mandatory)]
        [string] $Path,

        [Parameter(Mandatory)]
        [VMware.Binding.OpenApi.Client.RequestOptions] $Options
    )

    $client = $Connection.Client
    $configuration = $Connection.Configuration

    switch ($Method) {
        'GET' { return $client.Get[System.Object]($Path, $Options, $configuration) }
        'POST' { return $client.Post[System.Object]($Path, $Options, $configuration) }
        'PUT' { return $client.Put[System.Object]($Path, $Options, $configuration) }
        'DELETE' { return $client.Delete[System.Object]($Path, $Options, $configuration) }
        'PATCH' { return $client.Patch[System.Object]($Path, $Options, $configuration) }
    }
}

# ---------------------------------------------------------------------------
# Public surface.
# ---------------------------------------------------------------------------

function Save-VcfOnDiscoveredApplication {
    <#
    .SYNOPSIS
        Saves every discovered application of one discovery source in VCF
        Operations for Networks and waits for the bulk save task to finish.

    .DESCRIPTION
        NOT IMPLEMENTED.

        The authoritative request shape is docs/contract.json, projected from
        the pinned OpenAPI specification recorded in docs/official_sources.json.

        The workflow uses four operations, in this order:

          1. create                          POST /api/ni/auth/token
          2. getDiscoveredApplications       GET  /api/ni/groups/discovered-applications
                                             (repeat while the response carries a cursor)
          3. saveDiscoveredApplications      POST /api/ni/groups/discovered-applications/save
          4. getBulkApplicationTaskProgress  GET  /api/ni/groups/task/progress/{requestId}
                                             (poll until the task is terminal)

    .PARAMETER Server
        Scheme, host and port of the VCF Operations for Networks appliance, for
        example https://vcfon.example.com. The /api/ni base path is appended by
        New-NiApiConnection.

    .PARAMETER Credential
        Credentials for the create operation.

    .PARAMETER DomainType
        Optional authentication domain type.

    .PARAMETER DomainValue
        Optional authentication domain value.

    .PARAMETER DiscoveryType
        Required discovery source. Used both as the discovery_type query
        parameter of getDiscoveredApplications and as the discovery_type body
        field of saveDiscoveredApplications.

    .PARAMETER Granularity
        Optional discovered-application granularity.

    .PARAMETER PageSize
        Optional page size for getDiscoveredApplications.

    .PARAMETER EnableIntent
        Optional. When the caller does not bind this parameter the field must
        not be sent at all, so that the server applies its documented default.

    .PARAMETER PollIntervalSeconds
        Delay between two getBulkApplicationTaskProgress calls.

    .PARAMETER TimeoutSeconds
        Upper bound on the time spent polling before the task is abandoned.

    .PARAMETER SkipCertificateCheck
        Accept any TLS certificate presented by Server.

    .OUTPUTS
        A PSCustomObject with these properties:
          RequestId           request_id returned by saveDiscoveredApplications
          Status              terminal status reported by the task
          Progress            terminal progress reported by the task
          TaskName            task_name reported by the task
          PollCount           number of getBulkApplicationTaskProgress calls made
          DiscoveredEntityIds entity_id values collected from every page
          SavedApplications   app_save_response array from the terminal poll
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Server,

        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [pscredential] $Credential,

        [Parameter()]
        [ValidateSet('LDAP', 'LOCAL')]
        [string] $DomainType,

        [Parameter()]
        [ValidateNotNullOrEmpty()]
        [string] $DomainValue,

        [Parameter(Mandatory)]
        [ValidateSet('SERVICE_NOW', 'FLOW_BASED_DISCOVERY')]
        [string] $DiscoveryType,

        [Parameter()]
        [ValidateSet('FINE', 'MEDIUM', 'COARSE')]
        [string] $Granularity,

        [Parameter()]
        [ValidateRange(1, 1000)]
        [int] $PageSize,

        [Parameter()]
        [bool] $EnableIntent,

        [Parameter()]
        [ValidateRange(0, 3600)]
        [double] $PollIntervalSeconds = 5,

        [Parameter()]
        [ValidateRange(1, 86400)]
        [double] $TimeoutSeconds = 300,

        [Parameter()]
        [switch] $SkipCertificateCheck
    )

    throw [System.NotImplementedException]::new(
        'Save-VcfOnDiscoveredApplication is not implemented yet.')
}

Export-ModuleMember -Function 'Save-VcfOnDiscoveredApplication'
