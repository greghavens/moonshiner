Set-StrictMode -Version Latest

function Invoke-VcfNsxPartialRollout {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [VMware.Bindings.Nsx.Policy.Api.PolicyApi] $PolicyApi,

        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [VMware.Bindings.Nsx.Policy.Api.DfwSecurityPolicyApi]
        $DfwSecurityPolicyApi,

        [Parameter(Mandatory)]
        [string] $DomainId,

        [Parameter(Mandatory)]
        [string] $GroupId,

        [Parameter(Mandatory)]
        [string] $SecurityPolicyId,

        [Parameter(Mandatory)]
        [string] $GroupDisplayName,

        [Parameter(Mandatory)]
        [string[]] $IpAddress,

        [Parameter(Mandatory)]
        [string] $PolicyDisplayName,

        [Parameter(Mandatory)]
        [string] $RuleDisplayName,

        [Parameter(Mandatory)]
        [string] $DestinationGroupPath,

        [Parameter(Mandatory)]
        [uint32] $PolicySequenceNumber,

        [Parameter(Mandatory)]
        [uint32] $RuleSequenceNumber,

        [Parameter(Mandatory)]
        [string] $ReportPath,

        [string] $GroupDescription,

        [string] $PolicyDescription,

        [string] $RuleNotes
    )

    throw 'TODO: build the generated models, apply both steps, and preserve partial results.'
}

Export-ModuleMember -Function Invoke-VcfNsxPartialRollout
