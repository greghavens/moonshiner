Set-StrictMode -Version Latest

function Start-VcfInstallerBundleDownload {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $ServerUri,

        [Parameter(Mandatory)]
        [string] $AccessToken,

        [Parameter(Mandatory)]
        [string] $BundleId,

        [ValidateRange(0, [int]::MaxValue)]
        [int] $PollIntervalMilliseconds = 0
    )

    throw 'Not implemented.'
}

Export-ModuleMember -Function Start-VcfInstallerBundleDownload
