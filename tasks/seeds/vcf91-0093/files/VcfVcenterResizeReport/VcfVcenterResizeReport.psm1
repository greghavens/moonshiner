Set-StrictMode -Version Latest

function New-VcfVcenterResizeClient {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $Server,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $SessionToken,

        [switch] $SkipCertificateCheck
    )

    throw [NotImplementedException]::new(
        'TODO: create a VCF PowerCLI-backed vCenter resize client.'
    )
}

function Set-VcfVmResizeAndStart {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject] $Client,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Vm,

        [Parameter(Mandatory)]
        [ValidateRange(1, [long]::MaxValue)]
        [long] $CpuCount,

        [Parameter(Mandatory)]
        [ValidateRange(1, [long]::MaxValue)]
        [long] $MemoryMiB
    )

    throw [NotImplementedException]::new(
        'TODO: resize, start, and return an ordered partial-failure report.'
    )
}

Export-ModuleMember -Function @(
    'New-VcfVcenterResizeClient',
    'Set-VcfVmResizeAndStart'
)
