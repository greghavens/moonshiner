Set-StrictMode -Version Latest

function Start-VcfInstallerBundleDownload {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $BaseUri,

        [Parameter(Mandatory)]
        [pscredential] $Credential,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $BundleId,

        [Parameter()]
        [ValidateRange(0, 60000)]
        [int] $PollIntervalMilliseconds = 0
    )

    throw 'Start-VcfInstallerBundleDownload has not been implemented.'
}

Export-ModuleMember -Function Start-VcfInstallerBundleDownload
