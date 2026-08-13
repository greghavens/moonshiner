Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module -Name 'VMware.Sdk.Vcf.SddcManager' `
    -MinimumVersion '13.5.0.25380678' `
    -ErrorAction Stop

function Export-VcfResilientDomainInventory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [object] $Server,
        [Parameter(Mandatory)] [string] $RefreshTokenId,
        [Parameter(Mandatory)] [string] $Path,
        [ValidateRange(1, 1000)] [int] $PageSize = 100,
        [string] $Type
    )

    throw [System.NotImplementedException]::new(
        'Implement the contract-pinned resilient domain export.'
    )
}

Export-ModuleMember -Function 'Export-VcfResilientDomainInventory'
