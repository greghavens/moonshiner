Set-StrictMode -Version Latest

function New-VcfVcenterCredentialClient {
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
        'TODO: create a credential-generation client without sending a request.'
    )
}

function Get-VcfVcenterAuthorizationRole {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [psobject] $Client
    )

    throw [NotImplementedException]::new(
        'TODO: lease one credential generation and list authorization roles.'
    )
}

function Set-VcfVcenterCredential {
    [CmdletBinding(DefaultParameterSetName = 'Token')]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [psobject] $Client,

        [Parameter(Mandatory, ParameterSetName = 'Connection')]
        [VMware.Sdk.OpenApi.Cmdlets.IServerConnection] $Connection,

        [Parameter(ParameterSetName = 'Token')]
        [Parameter(ParameterSetName = 'Connection')]
        [uri] $Server,

        [Parameter(Mandatory, ParameterSetName = 'Token')]
        [ValidateNotNullOrEmpty()]
        [string] $SessionToken,

        [Parameter(ParameterSetName = 'Token')]
        [switch] $SkipCertificateCheck
    )

    throw [NotImplementedException]::new(
        'TODO: drain the old generation and publish the new credential.'
    )
}

Export-ModuleMember -Function @(
    'New-VcfVcenterCredentialClient',
    'Get-VcfVcenterAuthorizationRole',
    'Set-VcfVcenterCredential'
)
