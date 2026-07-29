Set-StrictMode -Version Latest

function New-VcfVcenterRoleClient {
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
        'TODO: create a VCF PowerCLI-backed vCenter role client.'
    )
}

function Get-VcfVcenterRoleCollection {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject] $Client,

        [ValidateRange(1, [long]::MaxValue)]
        [long] $PageSize = 200
    )

    throw [NotImplementedException]::new(
        'TODO: retrieve every role page and return a stable ordinal ordering.'
    )
}

Export-ModuleMember -Function @(
    'New-VcfVcenterRoleClient',
    'Get-VcfVcenterRoleCollection'
)
