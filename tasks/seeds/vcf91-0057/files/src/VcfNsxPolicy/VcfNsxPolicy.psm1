Set-StrictMode -Version Latest

function New-VcfNsxPolicyClient {
    [CmdletBinding(DefaultParameterSetName = 'Token')]
    param(
        [Parameter(Mandatory, ParameterSetName = 'Connection')]
        [VMware.Sdk.OpenApi.Cmdlets.IServerConnection] $Connection,

        [Parameter(Mandatory, ParameterSetName = 'Token')]
        [Parameter(ParameterSetName = 'Connection')]
        [uri] $Server,

        [Parameter(Mandatory, ParameterSetName = 'Token')]
        [ValidateNotNullOrEmpty()]
        [string] $AccessToken,

        [Parameter(ParameterSetName = 'Token')]
        [switch] $SkipCertificateCheck
    )

    throw [System.NotImplementedException]::new(
        'Create the contract-backed NSX Policy client.'
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
