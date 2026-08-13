Set-StrictMode -Version Latest

<#
.SYNOPSIS
    Runs the nightly alert triage pass against VCF Operations.

.DESCRIPTION
    Not implemented yet. See README.md for the behaviour this has to have and
    docs/contract.json for the operations it is allowed to call.
#>
function Invoke-VcfOpsAlertTriage {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Server,
        [Parameter(Mandatory)] [pscredential] $Credential,
        [Parameter(Mandatory)] [string] $Action,
        [string]   $AuthSource,
        [int]      $SuspendMinutes,
        [string]   $OwnerAccountId,
        [int]      $PageSize = 100,
        [switch]   $ActiveOnly,
        [string[]] $AlertCriticality,
        [string]   $ResourceKind
    )

    throw [System.NotImplementedException]::new('Invoke-VcfOpsAlertTriage is not implemented.')
}

Export-ModuleMember -Function 'Invoke-VcfOpsAlertTriage'
