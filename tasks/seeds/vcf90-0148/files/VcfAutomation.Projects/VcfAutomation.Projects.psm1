Set-StrictMode -Version Latest

function Get-VcfAutomationProjectInventory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $Server,

        [Parameter(Mandatory)]
        [string] $AccessToken,

        [int] $PageSize = 100,

        [string] $ApiVersion,

        [string] $Filter
    )

    throw 'Get-VcfAutomationProjectInventory is not implemented.'
}

Export-ModuleMember -Function Get-VcfAutomationProjectInventory
