Set-StrictMode -Version Latest

function Set-VcfInstallerProxyConfiguration {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object] $Server,

        [Parameter(Mandatory)]
        [bool] $IsEnabled,

        [Alias('Host')]
        [string] $ProxyHost,

        [ValidateRange(1, 65535)]
        [int] $Port,

        [ValidateSet('HTTP', 'HTTPS')]
        [string] $TransferProtocol,

        [string] $Username,

        [string] $Password,

        [bool] $IsAuthenticated,

        [ValidateRange(0, 3600)]
        [int] $PollIntervalSeconds = 2,

        [ValidateRange(1, 86400)]
        [int] $TimeoutSeconds = 300
    )

    throw 'Set-VcfInstallerProxyConfiguration has not been implemented.'
}

Export-ModuleMember -Function Set-VcfInstallerProxyConfiguration
