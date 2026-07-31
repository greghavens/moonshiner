Set-StrictMode -Version Latest

function Ensure-VcfOpsLogForwarder {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [uri] $BaseUri,

        [Parameter(Mandatory)]
        [string] $LogToken,

        [Parameter(Mandatory)]
        [string] $Name,

        [Parameter(Mandatory)]
        [string] $Host,

        [Parameter(Mandatory)]
        [ValidateRange(1, 65535)]
        [int] $Port,

        [ValidateSet('SYSLOG', 'RAW', 'RAWPLUS', IgnoreCase = $false)]
        [string] $Protocol = 'SYSLOG',

        [ValidateSet('TCP', 'UDP', IgnoreCase = $false)]
        [string] $TransportProtocol = 'TCP',

        [bool] $SslEnabled = $true,

        [bool] $Enabled = $true,

        [Net.Http.HttpClient] $HttpClient
    )

    throw [NotImplementedException]::new(
        'TODO: ensure the named log forwarder without duplicating it.'
    )
}

Export-ModuleMember -Function Ensure-VcfOpsLogForwarder
