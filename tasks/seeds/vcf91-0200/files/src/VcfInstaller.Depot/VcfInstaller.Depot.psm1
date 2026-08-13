Set-StrictMode -Version Latest

function Set-VcfInstallerDepotToken {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object] $Server,

        [Parameter(Mandatory)]
        [ValidateLength(1, 32)]
        [string] $DownloadToken,

        [string] $DownloadActivationCode,

        [ValidateRange(0, 10)]
        [int] $RetryCount = 2,

        [ValidateRange(0, 300)]
        [int] $RetryDelaySeconds = 1
    )

    throw 'Set-VcfInstallerDepotToken has not been implemented.'
}

Export-ModuleMember -Function Set-VcfInstallerDepotToken
