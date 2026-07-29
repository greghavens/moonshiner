Set-StrictMode -Version Latest

function New-VcfNsxIpAddressBlockModel {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$DisplayName,
        [Parameter(Mandatory)][string[]]$Cidrs,
        [string]$Description,
        [bool]$SubnetExclusive
    )

    throw 'Not implemented'
}

function Set-VcfNsxIpAddressBlock {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][uri]$BaseUri,
        [Parameter(Mandatory)][string]$IpBlockId,
        [Parameter(Mandatory)][string]$DisplayName,
        [Parameter(Mandatory)][string[]]$Cidrs,
        [Parameter(Mandatory)][string]$AccessToken,
        [string]$Description,
        [bool]$SubnetExclusive
    )

    throw 'Not implemented'
}

Export-ModuleMember -Function @(
    'New-VcfNsxIpAddressBlockModel'
    'Set-VcfNsxIpAddressBlock'
)
