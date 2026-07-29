Set-StrictMode -Version Latest

function Assert-VcfNsxCredentialGate {
    param(
        [Parameter(Mandatory)]
        [object] $Gate
    )

    if (
        $null -eq $Gate -or
        $Gate.PSObject.TypeNames -cnotcontains 'VcfNsxCredentialGate.State'
    ) {
        throw 'Gate was not created by New-VcfNsxCredentialGate.'
    }
    if (
        $Gate.Lock -isnot
            [System.Threading.ReaderWriterLockSlim] -or
        $Gate.PolicyApi -isnot
            [VMware.Bindings.Nsx.Policy.Api.PolicyApi]
    ) {
        throw 'Gate state is invalid.'
    }
}

function New-VcfNsxCredentialGate {
    [CmdletBinding()]
    [OutputType([object])]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [VMware.Bindings.Nsx.Policy.Api.PolicyApi] $PolicyApi
    )

    throw 'TODO: create an independent credential gate.'
}

function Get-VcfNsxGroupPage {
    [CmdletBinding()]
    [OutputType([VMware.Bindings.Nsx.Policy.Model.GroupListResult])]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [object] $Gate,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $DomainId
    )

    throw 'TODO: lease the current client and invoke ListGroupForDomain.'
}

function Set-VcfNsxCredential {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [object] $Gate,

        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [VMware.Bindings.Nsx.Policy.Api.PolicyApi] $PolicyApi
    )

    throw 'TODO: drain old-client requests before publishing the new client.'
}

Export-ModuleMember -Function @(
    'New-VcfNsxCredentialGate',
    'Get-VcfNsxGroupPage',
    'Set-VcfNsxCredential'
)
