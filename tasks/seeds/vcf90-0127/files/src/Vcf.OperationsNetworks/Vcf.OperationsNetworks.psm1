Set-StrictMode -Version Latest

function Add-VcfOperationsNetworksVCenterBatch {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $BaseUri,

        [Parameter(Mandatory)]
        [pscredential] $Credential,

        [Parameter(Mandatory)]
        [object[]] $VCenter
    )

    throw 'Add-VcfOperationsNetworksVCenterBatch is not implemented.'
}

Export-ModuleMember -Function Add-VcfOperationsNetworksVCenterBatch
