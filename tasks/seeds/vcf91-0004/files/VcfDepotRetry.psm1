Set-StrictMode -Version Latest

function Set-VcfServicesConfigRetrySafe {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [object] $Server,
        [Parameter(Mandatory)] [string] $ServiceName,
        [Parameter(Mandatory)] [string] $ServiceType,
        [Parameter(Mandatory)] [string] $ServiceKey,
        [Parameter(Mandatory)] [string] $NodeName,
        [Parameter(Mandatory)] [string] $AddressType,
        [Parameter(Mandatory)] [string] $AddressValue,
        [ValidateRange(1, 2147483647)] [int] $MaxAttempts = 2
    )

    throw [System.NotImplementedException]::new(
        'Implement the focused VMware SDK services-config update.'
    )
}

Export-ModuleMember -Function 'Set-VcfServicesConfigRetrySafe'
