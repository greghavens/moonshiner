Set-StrictMode -Version Latest

function Get-VcfInstallerTaskInventory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [object] $Server,

        [ValidateRange(1, 100)]
        [int] $PageSize = 100,

        [ValidateNotNullOrEmpty()]
        [string] $TaskStatus,

        [ValidateNotNullOrEmpty()]
        [string] $TaskType,

        [ValidateNotNullOrEmpty()]
        [string] $ResourceId,

        [ValidateNotNullOrEmpty()]
        [string] $ResourceType,

        [Nullable[long]] $CompletedAfter,

        [ValidateNotNullOrEmpty()]
        [string] $TaskName,

        [Nullable[bool]] $DoLiveRefresh
    )

    throw 'Get-VcfInstallerTaskInventory has not been implemented.'
}

Export-ModuleMember -Function Get-VcfInstallerTaskInventory
