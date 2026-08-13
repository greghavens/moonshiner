#Requires -Version 7.4

Set-StrictMode -Version Latest

# ---------------------------------------------------------------------------
# Transport helpers.
#
# These are already implemented. They wrap the OpenAPI binding layer that ships
# with the VMware.Sdk.Vcf PowerCLI modules (module VMware.OpenAPI, assembly
# VMware.Binding.OpenApi), which the environment installs as a prerequisite and
# which this repository never vendors.
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

            $options.PathParameters.Add('id', $entityId)        # {id} in the path
            $options.QueryParameters.Add('size', '100')         # ?size=...
            $options.HeaderParameters.Add('Authorization', $v)  # request header
            $options.Data = [ordered]@{ key = 'value' }         # JSON request body

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
        [ValidateSet('GET', 'POST')]
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
    }
}

function Get-NiField {
    <#
    .SYNOPSIS
        Reads one field from a deserialized JSON object, or $null when absent.
    #>
    [CmdletBinding()]
    param(
        [Parameter()]
        [AllowNull()]
        [object] $InputObject,

        [Parameter(Mandatory)]
        [string] $Name
    )

    if ($null -eq $InputObject) { return $null }

    $dictionary = $InputObject -as [System.Collections.Generic.IDictionary[string, object]]
    if ($null -ne $dictionary) {
        if ($dictionary.ContainsKey($Name)) { return $dictionary[$Name] }
        return $null
    }

    if ($InputObject.PSObject.Properties.Name -contains $Name) {
        return $InputObject.$Name
    }
    return $null
}

# ---------------------------------------------------------------------------
# Public surface.
# ---------------------------------------------------------------------------

<#
.SYNOPSIS
    Opens a session against a VCF Operations for Networks appliance.

.DESCRIPTION
    Calls the 'create' operation (POST /api/ni/auth/token) and returns a
    session object that Get-VcfOnApplication consumes.

    The returned object must expose at least:
        Connection - the object returned by New-NiApiConnection
        BaseUri    - the API base path, ending in /api/ni
        Token      - the token field of the Token response
        Expiry     - the expiry field of the Token response

    The authoritative request shape is docs/contract.json, projected from the
    pinned OpenAPI specification recorded in docs/official_sources.json.
#>
function Connect-VcfOnServer {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string] $Server,

        [Parameter(Mandatory)]
        [pscredential] $Credential,

        [Parameter()]
        [ValidateSet('LDAP', 'LOCAL')]
        [string] $DomainType,

        [Parameter()]
        [string] $DomainValue,

        [Parameter()]
        [switch] $SkipCertificateCheck
    )

    throw [System.NotImplementedException]::new('Connect-VcfOnServer is not implemented.')
}

<#
.SYNOPSIS
    Retrieves every application known to VCF Operations for Networks.

.DESCRIPTION
    Sweeps the 'listApplications' collection (GET /api/ni/groups/applications)
    to completion using the cursor pagination described in docs/contract.json,
    resolves each entity through 'getApplicationById'
    (GET /api/ni/groups/applications/{id}), and emits the results in the stable
    order the contract defines.
#>
function Get-VcfOnApplication {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object] $Session,

        [Parameter()]
        [ValidateRange(1, 1000)]
        [int] $PageSize = 100,

        [Parameter()]
        [long] $ModifiedAfter
    )

    throw [System.NotImplementedException]::new('Get-VcfOnApplication is not implemented.')
}

Export-ModuleMember -Function Connect-VcfOnServer, Get-VcfOnApplication
