Set-StrictMode -Version Latest

function Set-VcfAutomationPolicy {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $Server,

        [Parameter(Mandatory)]
        [string] $AccessToken,

        [Parameter(Mandatory)]
        [string] $TypeId,

        [string] $Id,
        [string] $Name,
        [string] $Description,
        [string] $ProjectId,

        [ValidateSet('SOFT', 'HARD')]
        [string] $EnforcementType,

        [System.Collections.IDictionary] $Definition,
        [System.Collections.IDictionary] $Criteria,
        [System.Collections.IDictionary] $ScopeCriteria,
        [string] $OpaRegoCriteria
    )

    throw 'Set-VcfAutomationPolicy is not implemented.'
}

Export-ModuleMember -Function Set-VcfAutomationPolicy
