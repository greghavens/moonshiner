Set-StrictMode -Version Latest

function New-VcfVcenterResizeClient {
    [CmdletBinding(DefaultParameterSetName = 'Token')]
    param(
        [Parameter(Mandatory, ParameterSetName = 'Connection')]
        [VMware.Sdk.OpenApi.Cmdlets.IServerConnection] $Connection,

        [Parameter(Mandatory, ParameterSetName = 'Token')]
        [Parameter(ParameterSetName = 'Connection')]
        [uri] $Server,

        [Parameter(Mandatory, ParameterSetName = 'Token')]
        [ValidateNotNullOrEmpty()]
        [string] $SessionToken,

        [Parameter(ParameterSetName = 'Token')]
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
