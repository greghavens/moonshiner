Set-StrictMode -Version Latest

function Connect-VcfInstallerChangeServer {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Server,

        [Parameter(Mandatory)]
        [pscredential]$Credential,

        [int]$Port = 443,

        [ValidateSet('http', 'https')]
        [string]$Protocol = 'https'
    )

    throw 'Not implemented.'
}

function Invoke-VcfInstallerChange {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object]$Connection,

        [Parameter(Mandatory)]
        [ValidateRange(1, 2147483647)]
        [int]$MaxAllowedDomainsInSubscription,

        [Parameter(Mandatory)]
        [string]$ProxyHost,

        [Parameter(Mandatory)]
        [ValidateRange(1, 65535)]
        [int]$ProxyPort,

        [ValidateSet('HTTP', 'HTTPS')]
        [string]$ProxyProtocol = 'HTTPS',

        [ValidateSet('ENABLE', 'DISABLE')]
        [string]$CeipStatus = 'DISABLE',

        [ValidateRange(0, 300)]
        [int]$PollIntervalSeconds = 1,

        [ValidateRange(1, 3600)]
        [int]$TaskTimeoutSeconds = 60
    )

    throw 'Not implemented.'
}

Export-ModuleMember -Function Connect-VcfInstallerChangeServer, Invoke-VcfInstallerChange
