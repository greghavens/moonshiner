Set-StrictMode -Version Latest

function New-VcfVcenterCpuClient {
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
