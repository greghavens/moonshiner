Set-StrictMode -Version Latest

<#
.SYNOPSIS
    Retrieves the complete SDDC Manager credential collection in a stable order.

.DESCRIPTION
    getCredentials (GET /v1/credentials) is a page-number paginated collection. A single
    call returns one page plus a pageMetadata block; the collection is only complete once
    every page has been read. See docs/contract.json for the pinned wire contract and
    docs/official_sources.json for its provenance.
#>
function Get-VcfSddcManagerCredentialInventory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object] $Server,

        [ValidateRange(1, 1000)]
        [int] $PageSize = 100,

        [string] $ResourceName,

        [ValidateSet('ESXI', 'VCENTER', 'PSC', 'NSXT_MANAGER', 'NSXT_EDGE', 'NSX_ALB', 'BACKUP')]
        [string] $ResourceType,

        [string] $DomainName,

        [ValidateSet('USER', 'SYSTEM', 'SERVICE')]
        [string] $AccountType
    )

    throw 'Get-VcfSddcManagerCredentialInventory has not been implemented.'
}

Export-ModuleMember -Function Get-VcfSddcManagerCredentialInventory
