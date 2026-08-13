Set-StrictMode -Version Latest

function Set-VcfInstallerDepotToken {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [object] $Server,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $DownloadToken
    )

    throw 'Set-VcfInstallerDepotToken is not implemented.'
}

Export-ModuleMember -Function Set-VcfInstallerDepotToken
