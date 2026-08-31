Set-StrictMode -Version Latest

function New-VcfVcenterCredentialClient {
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
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [psobject] $Client,

        [Parameter()]
        [uri] $Server,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $SessionToken,

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
