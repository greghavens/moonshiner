<#
.SYNOPSIS
    Builds the ResourceActionRequest body for
    POST /deployment/api/deployments/{deploymentId}/resources/{resourceId}/requests.

.DESCRIPTION
    Client-side only; sends nothing. Same rule as every other Initialize-* body builder:
    an unbound parameter is absent from the returned body.

    Field names come from docs/contract.json ->
    operations.submitResourceActionRequest.requestBody.

.OUTPUTS
    System.Collections.Specialized.OrderedDictionary
#>
function Initialize-VcfaResourceActionRequest {
    [CmdletBinding()]
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [string] $ActionId,

        [System.Collections.IDictionary] $Inputs,

        [string] $Reason
    )

    throw [System.NotImplementedException]::new(
        'Initialize-VcfaResourceActionRequest is not implemented yet.')
}
