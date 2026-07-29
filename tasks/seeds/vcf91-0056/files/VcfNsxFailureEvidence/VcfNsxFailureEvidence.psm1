Set-StrictMode -Version Latest

function Get-VcfNsxIntentFailureEvidence {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [VMware.Bindings.Nsx.Policy.Api.PolicyRealizedStateApi] $RealizedStateApi,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $IntentPath,

        [ValidateRange(1, 1000)]
        [uint64] $PageSize = 500
    )

    throw 'TODO: read the intent status and correlate every alarm page by exact intent path.'
}

Export-ModuleMember -Function Get-VcfNsxIntentFailureEvidence
