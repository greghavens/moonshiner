Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module -Name 'VMware.Sdk.Vcf.SddcManager' `
    -MinimumVersion '13.5.0.25380678' `
    -ErrorAction Stop

function Get-VcfDomainClusterMap {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [object] $Server,
        [Parameter(Mandatory)] [string] $RefreshTokenId,
        [ValidateRange(1, 1000)] [int] $PageSize = 100
    )

    throw [System.NotImplementedException]::new(
        'Implement the contract-pinned refresh-safe domain and cluster map.'
    )
}

Export-ModuleMember -Function 'Get-VcfDomainClusterMap'
