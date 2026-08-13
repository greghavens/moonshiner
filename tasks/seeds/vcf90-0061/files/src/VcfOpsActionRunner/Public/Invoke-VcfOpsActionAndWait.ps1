function Invoke-VcfOpsActionAndWait {
    <#
    .SYNOPSIS
        Submits a VCF Operations action and waits for it to reach a terminal state.

    .DESCRIPTION
        Submitting an action returns task identifiers, not a result. This function
        submits the action and then polls its task until the appliance reports a
        terminal state, or until -TimeoutSeconds elapses.

        The wire shape of both calls is fixed by docs/contract.json.

    .PARAMETER Server
        A connected VCF Operations server, as returned by Connect-VcfOpsServer.

    .PARAMETER ActionId
        Identifier of the action to run.

    .PARAMETER ResourceId
        One or more resource UUIDs the action runs against. Each becomes its own
        parameter group.

    .PARAMETER ContextId
        Optional. Names the action step to execute.

    .PARAMETER ContextResourceId
        Optional. Resource UUIDs that give the action its context.

    .PARAMETER Parameter
        Optional. Name/value inputs applied to every parameter group.

    .PARAMETER IncludeDetail
        Ask the appliance for per-object detail on each status poll.

    .PARAMETER PollIntervalSeconds
        Delay between status polls.

    .PARAMETER TimeoutSeconds
        Give up if no terminal state is reached within this many seconds.

    .OUTPUTS
        A single object with ActionId, TaskId, State, Succeeded, PollCount,
        Messages and Status properties.

    .EXAMPLE
        Invoke-VcfOpsActionAndWait -Server $ops -ActionId 'PowerOffVM' -ResourceId $vmUuid
    #>
    [CmdletBinding()]
    [OutputType([psobject])]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        $Server,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string]$ActionId,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string[]]$ResourceId,

        [string]$ContextId,

        [string[]]$ContextResourceId,

        [hashtable]$Parameter,

        [switch]$IncludeDetail,

        [ValidateRange(0, 3600)]
        [int]$PollIntervalSeconds = 5,

        [ValidateRange(1, 86400)]
        [int]$TimeoutSeconds = 900
    )

    throw [System.NotImplementedException]::new(
        'Invoke-VcfOpsActionAndWait is not implemented yet.')
}
