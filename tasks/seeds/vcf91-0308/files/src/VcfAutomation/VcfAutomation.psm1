Set-StrictMode -Version Latest

<#
.SYNOPSIS
    Submits one day-2 action request against every VCF Automation deployment in the tenant.

.DESCRIPTION
    Pages through the VCF Automation deployments of the calling tenant, then submits one
    deployment action request per deployment, in ascending deployment-name order.

    The bearer access token supplied to this function expires part way through the sweep.
    When that happens the function must exchange the API (refresh) token for a replacement
    access token and resume the interrupted request, without replaying deployments whose
    action request has already been accepted.

    See docs/contract.json for the wire contract these calls must honor.

.OUTPUTS
    One object per deployment, with exactly the properties DeploymentId, DeploymentName,
    RequestId and Status, ordered by DeploymentName ascending.
#>
function Invoke-VcfaDeploymentActionSweep {
    [CmdletBinding()]
    [OutputType([psobject[]])]
    param(
        # Base URL of the VCF Automation appliance, for example https://vcfa.example.com
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $ApiEndpoint,

        # Organization (tenant) name used in the access-token path.
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Tenant,

        # The API (refresh) token used to obtain a replacement access token.
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $ApiToken,

        # The bearer access token currently held by the caller.
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $AccessToken,

        # Identifier of the day-2 action to request on each deployment.
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $ActionId,

        # Optional reason recorded against each day-2 request.
        [Parameter()]
        [string] $Reason,

        # Optional inputs passed to the day-2 action.
        [Parameter()]
        [System.Collections.IDictionary] $Inputs,

        # Deployments requested per page.
        [Parameter()]
        [ValidateRange(1, 2000)]
        [int] $PageSize = 20,

        # Request timeout in seconds.
        [Parameter()]
        [ValidateRange(1, 600)]
        [int] $TimeoutSec = 30
    )

    throw [System.NotImplementedException]::new('Invoke-VcfaDeploymentActionSweep is not implemented yet.')
}

Export-ModuleMember -Function 'Invoke-VcfaDeploymentActionSweep'
