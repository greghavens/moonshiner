Set-StrictMode -Version Latest

function New-VcfVCenterClient {
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

    throw [System.NotImplementedException]::new(
        'Create the contract-backed vCenter Automation API client.'
    )
}

function Invoke-VcfVmClone {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject] $Client,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $SourceVm,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Name,

        [ValidateNotNullOrEmpty()]
        [string] $Folder,

        [ValidateNotNullOrEmpty()]
        [string] $ResourcePool,

        [ValidateNotNullOrEmpty()]
        [string] $Host,

        [ValidateNotNullOrEmpty()]
        [string] $Cluster,

        [ValidateNotNullOrEmpty()]
        [string] $Datastore,

        [switch] $PowerOn,

        [ValidateRange(1, [int]::MaxValue)]
        [int] $TimeoutSeconds = 300,

        [ValidateRange(0, [int]::MaxValue)]
        [int] $PollIntervalMilliseconds = 1000
    )

    throw [System.NotImplementedException]::new(
        'Submit Vcenter.VM_clone$Task and poll Cis.Tasks_get to a terminal state.'
    )
}

Export-ModuleMember -Function @(
    'New-VcfVCenterClient'
    'Invoke-VcfVmClone'
)
