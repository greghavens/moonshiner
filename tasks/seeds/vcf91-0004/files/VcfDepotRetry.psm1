Set-StrictMode -Version Latest

function Set-VcfDepotSettingsRetrySafe {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [object] $Server,
        [Parameter(Mandatory)] [string] $DownloadToken,
        [string] $Username,
        [securestring] $Password,
        [string] $DownloadActivationCode,
        [ValidateRange(1, 2147483647)] [int] $MaxAttempts = 2
    )

    throw [System.NotImplementedException]::new(
        'Implement the contract-pinned VMware SDK depot update.'
    )
}

Export-ModuleMember -Function 'Set-VcfDepotSettingsRetrySafe'
