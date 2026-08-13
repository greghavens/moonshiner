Set-StrictMode -Version Latest

function New-VcfVcenterCloneInventoryClient {
    [CmdletBinding(DefaultParameterSetName = 'Token')]
    param(
        [Parameter(Mandatory, ParameterSetName = 'Connection')]
        [object] $Connection,

        [Parameter(Mandatory, ParameterSetName = 'Token')]
        [Parameter(ParameterSetName = 'Connection')]
        [uri] $Server,

        [Parameter(Mandatory, ParameterSetName = 'Token')]
        [string] $SessionToken,

        [Parameter(ParameterSetName = 'Token')]
        [switch] $SkipCertificateCheck
    )

    throw [NotImplementedException]::new(
        'TODO: create a VCF PowerCLI-backed vCenter clone-inventory client.'
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
