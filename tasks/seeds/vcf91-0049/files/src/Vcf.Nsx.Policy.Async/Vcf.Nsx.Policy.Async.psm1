Set-StrictMode -Version Latest

function New-VcfNsxPowerCliTransport {
    [CmdletBinding()]
    param()

    throw 'TODO: create the VMware.Sdk.Nsx.Policy transport.'
}

function Set-VcfNsxInfraSegment {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $SegmentId,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $DisplayName,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $GatewayAddress,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $ConnectivityPath,

        [ValidateNotNullOrEmpty()]
        [string] $Description,

        [ValidateNotNullOrEmpty()]
        [string] $TransportZonePath,

        [ValidateSet('UP', 'DOWN')]
        [string] $AdminState,

        [ValidateRange(0, 2147483647)]
        [int] $OverlayId,

        [ValidateNotNullOrEmpty()]
        [string[]] $VlanIds,

        [ValidateRange(0, 60000)]
        [int] $PollIntervalMilliseconds = 1000,

        [ValidateRange(1, 86400)]
        [int] $TimeoutSeconds = 300,

        [scriptblock] $Transport
    )

    throw 'TODO: submit PatchInfraSegment and poll ReadIntentStatus.'
}

Export-ModuleMember -Function @(
    'New-VcfNsxPowerCliTransport',
    'Set-VcfNsxInfraSegment'
)
