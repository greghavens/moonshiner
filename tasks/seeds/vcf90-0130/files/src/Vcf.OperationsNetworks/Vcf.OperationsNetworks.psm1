Set-StrictMode -Version Latest

function Invoke-VcfOperationsNetworksVcenterChange {
    <#
    .SYNOPSIS
    Updates and enables a VCF Operations for Networks vCenter data source.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $ServerUri,

        [Parameter(Mandatory)]
        [string] $Token,

        [Parameter(Mandatory)]
        [string] $Id,

        [Parameter(Mandatory)]
        [string] $Nickname,

        [Parameter()]
        [AllowEmptyString()]
        [string] $Notes,

        [Parameter()]
        [pscredential] $Credential
    )

    throw 'Not implemented.'
}

Export-ModuleMember -Function Invoke-VcfOperationsNetworksVcenterChange
