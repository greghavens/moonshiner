Set-StrictMode -Version Latest

function Set-VcfNsxGroupDisplayName {
    [CmdletBinding()]
    [OutputType([VMware.Bindings.Nsx.Policy.Model.Group])]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [VMware.Bindings.Nsx.Policy.Api.PolicyApi] $PolicyApi,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $DomainId,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $GroupId,

        [Parameter(Mandatory)]
        [int32] $ExpectedRevision,

        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string] $ExpectedDisplayName,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $DisplayName,

        [string] $Description
    )

    throw 'TODO: read and validate the group before issuing the guarded update.'
}

Export-ModuleMember -Function Set-VcfNsxGroupDisplayName
