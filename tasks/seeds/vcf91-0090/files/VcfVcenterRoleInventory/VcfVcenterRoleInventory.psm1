Set-StrictMode -Version Latest

function New-VcfVcenterRoleInventorySession {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        $Server,

        [Parameter(Mandatory)]
        [scriptblock] $RefreshConnection,

        [scriptblock] $OperationInvoker
    )

    throw 'TODO: create a resumable vCenter role-inventory session.'
}

function Get-VcfVcenterRoleInventory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        $Session,

        [ValidateRange(1, [long]::MaxValue)]
        [long] $PageSize = 200
    )

    throw 'TODO: retrieve all role pages with one-request token refresh.'
}

Export-ModuleMember -Function @(
    'New-VcfVcenterRoleInventorySession',
    'Get-VcfVcenterRoleInventory'
)
