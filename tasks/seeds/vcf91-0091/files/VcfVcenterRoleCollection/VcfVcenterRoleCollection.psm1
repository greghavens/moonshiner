Set-StrictMode -Version Latest

function New-VcfVcenterRoleClient {
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
