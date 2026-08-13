Set-StrictMode -Version Latest

function Invoke-VcfOpsLogsForwarderRollout {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $ServerUri,

        [Parameter(Mandatory)]
        [pscredential] $Credential,

        [Parameter(Mandatory)]
        [ValidateSet('Local', 'ActiveDirectory', 'vIDM')]
        [string] $Provider,

        [Parameter(Mandatory)]
        [object[]] $Updates
    )

    throw 'TODO: implement the contract-pinned forwarder rollout.'
}

Export-ModuleMember -Function Invoke-VcfOpsLogsForwarderRollout
