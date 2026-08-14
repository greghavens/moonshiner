Set-StrictMode -Version Latest

function New-VsanProtectionGroupSnapshot {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $ServerUri,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $SessionId,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $ClusterId,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $ProtectionGroupId,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Name,

        [Parameter()]
        [ValidateRange(0, [int]::MaxValue)]
        [int] $PollIntervalMilliseconds = 0
    )

    throw 'New-VsanProtectionGroupSnapshot has not been implemented.'
}

Export-ModuleMember -Function New-VsanProtectionGroupSnapshot
