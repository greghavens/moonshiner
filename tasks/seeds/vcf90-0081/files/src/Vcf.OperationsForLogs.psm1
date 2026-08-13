Set-StrictMode -Version Latest

function Invoke-VcfLogsUpgrade {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $ServerUri,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $SessionId,

        [Parameter(Mandatory)]
        [uri] $PakUrl,

        [Parameter()]
        [ValidateRange(0, [int]::MaxValue)]
        [int] $PollIntervalMilliseconds = 1000,

        [Parameter()]
        [switch] $SkipCertificateCheck
    )

    throw 'Invoke-VcfLogsUpgrade has not been implemented.'
}

Export-ModuleMember -Function Invoke-VcfLogsUpgrade
