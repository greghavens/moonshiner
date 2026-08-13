<#
    VcfSddcLcm - retry-safe support bundle generation against the VCF 9.1
    SDDC LCM service.

    The wire contract for this module is docs/contract.json, derived from
    specifications/sddc-lcm/sddc-lcm-openapi.yaml in vmware/vcf-api-specs.
    See docs/official_sources.json for the exact commit.

    VMware.Sdk.Vcf 13.5.0 ships no cmdlet for generateComponentSupportBundle or
    getComponentSupportBundles, so those calls must be issued directly against
    the documented wire contract. The SDK is still required: it supplies the
    connection/authentication context consumed by New-VcfSddcLcmSession.

    TODO: implement the functions below. See README.md.
#>

Set-StrictMode -Version Latest

# From the spec's servers[0].url (https://vcf.broadcom.com/sddc-lcm).
$script:BasePath = '/sddc-lcm'

$script:TerminalStatus = @('SUCCEEDED', 'FAILED', 'CANCELED')

function New-VcfSddcLcmSession {
    <#
    .SYNOPSIS
        Creates a session for the SDDC LCM service on a VCF appliance.
    .DESCRIPTION
        Either pass an existing VMware.Sdk.Vcf connection (as returned by
        Connect-VcfSddcManagerServer) or supply -Server and -Token explicitly.
        SDDC LCM is served from the same appliance under the /sddc-lcm base path
        and authenticates with the same bearer token.
    #>
    [CmdletBinding(DefaultParameterSetName = 'Explicit')]
    param(
        [Parameter(Mandatory, ParameterSetName = 'Explicit')]
        [uri] $Server,

        [Parameter(Mandatory, ParameterSetName = 'Explicit')]
        [string] $Token,

        [Parameter(Mandatory, ParameterSetName = 'Connection')]
        [object] $Connection
    )

    throw [System.NotImplementedException]::new('New-VcfSddcLcmSession is not implemented.')
}

function Start-VcfSddcLcmSupportBundle {
    <#
    .SYNOPSIS
        Generates a support bundle for an SDDC LCM component, safely to retry.

    .DESCRIPTION
        generateComponentSupportBundle is not idempotent server-side: every call
        mints a new task and a new bundle. This function must make the operation
        retry-safe by tagging the request with a caller-supplied correlation ID
        and checking, before every POST, whether a task already carries that ID.
        Re-running with the same -CorrelationId must adopt the in-flight or
        completed task instead of generating a second bundle.

    .PARAMETER CorrelationId
        Stable idempotency key. Reuse the same value to retry an operation.

    .PARAMETER LookBackWindow
        Optional. Hours of history to include. When omitted the property must be
        omitted from the request body entirely so the service applies its own
        default; sending 0 or null would instead pin the window explicitly.

    .OUTPUTS
        PSCustomObject with Task, Reused, and (with -Wait) SupportBundle.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Session,

        [Parameter(Mandatory)]
        [ValidatePattern('^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')]
        [string] $ComponentId,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $CorrelationId,

        [int] $LookBackWindow,

        [switch] $Wait,
        [int] $TimeoutSeconds = 900,
        [int] $PollIntervalSeconds = 5
    )

    # Implement:
    #   1. getTasks, filtered to this component, sending only the filters you
    #      mean - unused optional query parameters must not reach the wire.
    #   2. If a returned task already carries $CorrelationId, adopt it and
    #      return with Reused = $true, without issuing a POST.
    #   3. Otherwise POST generateComponentSupportBundle with the
    #      X-Correlation-Id header. Omit lookBackWindow from the body unless the
    #      caller bound -LookBackWindow.
    #   4. With -Wait, poll getTask to a terminal state, then resolve the bundle
    #      through getComponentSupportBundles.
    throw [System.NotImplementedException]::new('Start-VcfSddcLcmSupportBundle is not implemented.')
}

Export-ModuleMember -Function New-VcfSddcLcmSession, Start-VcfSddcLcmSupportBundle
