Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module -Name 'VMware.Sdk.Vcf.Ops' `
    -MinimumVersion '13.5.0.25380678' `
    -ErrorAction Stop

function Get-VcfOpsCollectionDiagnosis {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string] $Server,

        [Parameter(Mandatory)]
        [pscredential] $Credential,

        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string] $ObjectName,

        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string] $ResourceKind,

        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string] $AdapterKind,

        [ValidateSet('https', 'http')]
        [string] $Protocol = 'https',

        [ValidateRange(1, 65535)]
        [int] $Port = 443,

        [AllowEmptyString()]
        [string] $AuthSource = 'local',

        [int] $PageSize = 1000
    )

    throw [System.NotImplementedException]::new(
        'Get-VcfOpsCollectionDiagnosis is not implemented yet.'
    )
}

Export-ModuleMember -Function 'Get-VcfOpsCollectionDiagnosis'
