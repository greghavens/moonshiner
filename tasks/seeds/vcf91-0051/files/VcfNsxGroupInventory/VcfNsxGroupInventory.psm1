Set-StrictMode -Version Latest

function Get-VcfNsxPolicyGroupInventory {
    [CmdletBinding()]
    [OutputType([VMware.Bindings.Nsx.Policy.Model.Group])]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [VMware.Bindings.Nsx.Policy.Api.PolicyApi] $PolicyApi,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $DomainId,

        [ValidateRange(1, 1000)]
        [uint64] $PageSize = 250
    )

    throw 'TODO: retrieve every ListGroupForDomain page and sort the complete collection.'
}

Export-ModuleMember -Function Get-VcfNsxPolicyGroupInventory
