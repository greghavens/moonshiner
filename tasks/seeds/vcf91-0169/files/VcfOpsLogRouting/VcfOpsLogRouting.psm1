Set-StrictMode -Version Latest

function Invoke-VcfOpsLogRoutingChange {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [uri] $BaseUri,

        [Parameter(Mandatory)]
        [string] $LogToken,

        [Parameter(Mandatory)]
        [string] $AgentGroupId,

        [Parameter(Mandatory)]
        [bool] $AgentAutoUpdate,

        [Parameter(Mandatory)]
        [string] $ForwarderId,

        [Parameter(Mandatory)]
        [bool] $ForwarderEnabled,

        [Parameter(Mandatory)]
        [string] $TestHost,

        [Parameter(Mandatory)]
        [ValidateRange(1, 65535)]
        [int] $TestPort,

        [Parameter(Mandatory)]
        [ValidateSet('SYSLOG', 'RAW', 'RAWPLUS')]
        [string] $TestProtocol,

        [Parameter(Mandatory)]
        [bool] $TestSslEnabled,

        [Parameter(Mandatory)]
        [ValidateSet('TCP', 'UDP')]
        [string] $TestTransportProtocol,

        [Net.Http.HttpClient] $HttpClient
    )

    throw [NotImplementedException]::new(
        'TODO: apply the routing change and preserve partial outcomes.'
    )
}

Export-ModuleMember -Function Invoke-VcfOpsLogRoutingChange
