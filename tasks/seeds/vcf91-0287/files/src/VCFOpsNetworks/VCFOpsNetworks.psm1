Set-StrictMode -Version Latest

<#
    VCF Operations for Networks 9.1 application rollout helpers.

    The wire contract these functions must honour is docs/contract.json, which is
    derived from the product OpenAPI specification (see docs/official_sources.json).

    Nothing here is implemented yet.
#>

function Connect-VCFOpsNetworksServer {
    [CmdletBinding(DefaultParameterSetName = 'Credential')]
    param(
        [Parameter(Mandatory)]
        [string] $Server,

        [int] $Port = 443,

        [ValidateSet('https', 'http')]
        [string] $Protocol = 'https',

        [Parameter(Mandatory, ParameterSetName = 'Credential')]
        [pscredential] $Credential,

        [Parameter(ParameterSetName = 'Credential')]
        [ValidateSet('LDAP', 'LOCAL')]
        [string] $DomainType,

        [Parameter(ParameterSetName = 'Credential')]
        [string] $DomainValue,

        [Parameter(Mandatory, ParameterSetName = 'OpsToken')]
        [string] $OpsAccessToken,

        [switch] $SkipCertificateCheck
    )

    throw [System.NotImplementedException]::new('Connect-VCFOpsNetworksServer is not implemented.')
}

function New-VCFOpsNetworksApplication {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object] $Connection,

        [Parameter(Mandatory)]
        [string] $Name,

        [Parameter(Mandatory)]
        [hashtable[]] $Tier
    )

    throw [System.NotImplementedException]::new('New-VCFOpsNetworksApplication is not implemented.')
}

function Disconnect-VCFOpsNetworksServer {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object] $Connection
    )

    throw [System.NotImplementedException]::new('Disconnect-VCFOpsNetworksServer is not implemented.')
}

Export-ModuleMember -Function Connect-VCFOpsNetworksServer, New-VCFOpsNetworksApplication, Disconnect-VCFOpsNetworksServer
