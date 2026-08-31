Set-StrictMode -Version Latest

function New-VcfNsxPolicyClient {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [VMware.Sdk.Nsx.Policy.Types.NsxServer] $Connection
    )

    throw [System.NotImplementedException]::new(
        'Create the NSX Policy client from the authenticated PowerCLI connection.'
    )
}

function Get-VcfNsxPolicySegment {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject] $Client
    )

    throw [System.NotImplementedException]::new(
        'Implement ListAllInfraSegments with pagination and local sorting.'
    )
}

function Set-VcfNsxPolicySegment {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject] $Client,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $SegmentId,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [ValidateLength(1, 255)]
        [string] $DisplayName,

        [string] $ConnectivityPath,

        [string] $TransportZonePath,

        [ValidateRange(1, [int]::MaxValue)]
        [int] $TimeoutSeconds = 300,

        [ValidateRange(0, [int]::MaxValue)]
        [int] $PollIntervalMilliseconds = 1000
    )

    throw [System.NotImplementedException]::new(
        'Implement PatchInfraSegment and poll ReadIntentStatus to a terminal state.'
    )
}

Export-ModuleMember -Function @(
    'New-VcfNsxPolicyClient'
    'Get-VcfNsxPolicySegment'
    'Set-VcfNsxPolicySegment'
)
