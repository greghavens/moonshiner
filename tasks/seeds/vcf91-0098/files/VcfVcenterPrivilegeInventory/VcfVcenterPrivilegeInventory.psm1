Set-StrictMode -Version Latest

function New-VcfVcenterPrivilegeInventorySession {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        $Server,

        [Parameter(Mandatory)]
        [scriptblock] $RefreshConnection,

        [scriptblock] $OperationInvoker
    )

    throw 'TODO: create a resumable vCenter privilege-inventory session.'
}

function Get-VcfVcenterPrivilegeInventory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        $Session,

        [ValidateRange(1, [long]::MaxValue)]
        [long] $PageSize = 200
    )

    throw 'TODO: retrieve all privilege pages with one-request token refresh.'
}

Export-ModuleMember -Function @(
    'New-VcfVcenterPrivilegeInventorySession',
    'Get-VcfVcenterPrivilegeInventory'
)
