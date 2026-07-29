Set-StrictMode -Version Latest

function New-VcfVcenterCpuClient {
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
        'TODO: create a VCF PowerCLI-backed vCenter CPU client.'
    )
}

function Set-VcfVmCpuCount {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject] $Client,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Vm,

        [Parameter(Mandatory)]
        [ValidateRange(1, [long]::MaxValue)]
        [long] $Count
    )

    throw [NotImplementedException]::new(
        'TODO: reconcile the VM CPU count without duplicating a mutation.'
    )
}

Export-ModuleMember -Function @(
    'New-VcfVcenterCpuClient',
    'Set-VcfVmCpuCount'
)
