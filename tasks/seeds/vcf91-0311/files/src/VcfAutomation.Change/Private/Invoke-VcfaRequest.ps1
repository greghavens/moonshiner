# SHIPPED HTTP LAYER - do not modify.
#
# One place where a VCF Automation request is actually put on the wire. It attaches the
# bearer token, serialises the body exactly as handed to it, and - importantly - does NOT
# throw on an HTTP error status. Callers branch on IsSuccess/StatusCode so that a failing
# step can be reported rather than becoming an unhandled terminating error.

function Invoke-VcfaRequest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object] $Session,

        [Parameter(Mandatory)]
        [ValidateSet('GET', 'POST')]
        [string] $Method,

        # Fully substituted path, e.g. /catalog/api/items/1f2e.../request
        [Parameter(Mandatory)]
        [string] $Path,

        # Query parameters. Array values are emitted as a single comma-separated value,
        # which is how the reference documents string[] parameters.
        [System.Collections.IDictionary] $Query,

        # Serialised verbatim: whatever keys are present are the keys that get sent.
        [System.Collections.IDictionary] $Body
    )

    $uri = $Session.BaseUri.TrimEnd('/') + $Path
    if ($Query -and $Query.Count -gt 0) {
        $pairs = foreach ($key in $Query.Keys) {
            $raw = $Query[$key]
            $value = if ($raw -is [string]) { $raw }
                     elseif ($raw -is [System.Collections.IEnumerable]) { ($raw | ForEach-Object { [string] $_ }) -join ',' }
                     else { [string] $raw }
            '{0}={1}' -f [uri]::EscapeDataString([string] $key), [uri]::EscapeDataString($value)
        }
        $uri += '?' + ($pairs -join '&')
    }

    $params = @{
        Uri                = $uri
        Method             = $Method
        Headers            = @{ Authorization = "Bearer $($Session.AccessToken)" }
        SkipHttpErrorCheck = $true
        MaximumRedirection = 0
        ErrorAction        = 'Stop'
    }
    if ($PSBoundParameters.ContainsKey('Body')) {
        $params['Body'] = ConvertTo-Json -InputObject $Body -Depth 20 -Compress
        $params['ContentType'] = 'application/json'
    }

    $response = Invoke-WebRequest @params

    $rawBody = [string] $response.Content
    $parsed = $null
    if (-not [string]::IsNullOrWhiteSpace($rawBody)) {
        try { $parsed = ConvertFrom-Json -InputObject $rawBody } catch { $parsed = $null }
    }

    [pscustomobject] @{
        Method     = $Method
        Uri        = $uri
        StatusCode = [int] $response.StatusCode
        IsSuccess  = ([int] $response.StatusCode -ge 200 -and [int] $response.StatusCode -lt 300)
        Body       = $parsed
        RawBody    = $rawBody
    }
}

# The reference guides publish status codes for these operations but no error-body schema,
# so anything we pull out of an error body is best effort. Returns $null when the body
# carries nothing usable, in which case callers should fall back to the status code.
function Get-VcfaErrorMessage {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [AllowNull()]
        [object] $Response
    )

    if ($null -eq $Response) { return $null }

    $body = $Response.Body
    if ($null -ne $body) {
        foreach ($candidate in 'message', 'serverMessage', 'detail', 'error_message') {
            $property = $body.PSObject.Properties[$candidate]
            if ($property -and -not [string]::IsNullOrWhiteSpace([string] $property.Value)) {
                return [string] $property.Value
            }
        }
    }

    if (-not [string]::IsNullOrWhiteSpace([string] $Response.RawBody)) {
        return ([string] $Response.RawBody).Trim()
    }
    return $null
}
