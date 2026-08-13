Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# VCF PowerCLI 9.1 is an environment prerequisite. The generated SDK carries no
# cmdlets for the VCF Operations for networks /api/ni surface, so this module
# issues those requests itself, but it still loads inside the same PowerCLI
# session and must never vendor or imitate the VMware modules.
Import-Module -Name 'VMware.Sdk.Vcf.Ops' `
    -MinimumVersion '13.5.0.25380678' `
    -ErrorAction Stop

function New-VcfOpsNetworksApplication {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [string] $Server,

        [Parameter(Mandatory)]
        [pscredential] $Credential,

        [Parameter(Mandatory)]
        [string] $Name,

        [Parameter(Mandatory)]
        [hashtable[]] $Tier,

        [ValidateSet('https', 'http')]
        [string] $Protocol = 'https',

        [ValidateRange(1, 65535)]
        [int] $Port = 443,

        [ValidateSet('LOCAL', 'LDAP')]
        [string] $DomainType = 'LOCAL',

        [string] $DomainValue,

        [bool] $EnableIntent,

        [long] $LastModifiedTimestamp,

        [int] $PageSize = 100
    )

    throw [System.NotImplementedException]::new(
        'New-VcfOpsNetworksApplication is not implemented yet.'
    )
}

Export-ModuleMember -Function 'New-VcfOpsNetworksApplication'
