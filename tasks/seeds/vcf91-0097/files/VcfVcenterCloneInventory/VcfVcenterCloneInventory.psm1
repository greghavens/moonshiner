Set-StrictMode -Version Latest

function New-VcfVcenterCloneInventoryClient {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $Server,

        [Parameter(Mandatory)]
        [string] $SessionToken,

        [switch] $SkipCertificateCheck
    )

    throw [NotImplementedException]::new(
        'TODO: create a direct vCenter clone-inventory client.'
    )
}

function Invoke-VcfVcenterCloneInventory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject] $Client,

        [Parameter(Mandatory)]
        [string] $SourceVm,

        [Parameter(Mandatory)]
        [string] $Name,

        [ValidateRange(1, [int]::MaxValue)]
        [int] $MaxPolls = 20,

        [ValidateRange(0, [int]::MaxValue)]
        [int] $PollIntervalMilliseconds = 250
    )

    throw [NotImplementedException]::new(
        'TODO: submit, poll to terminal success, then return sorted VM inventory.'
    )
}

Export-ModuleMember -Function @(
    'New-VcfVcenterCloneInventoryClient',
    'Invoke-VcfVcenterCloneInventory'
)
