Set-StrictMode -Version Latest

function New-VcfOpsLogCredentialGate {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string] $SecretName,

        [Parameter(Mandatory)]
        [string] $Secret,

        [Parameter(Mandatory)]
        [string] $AccessToken
    )

    throw [NotImplementedException]::new(
        'TODO: construct the credential gate.'
    )
}

function Get-VcfOpsLogCredentialLease {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object] $Gate
    )

    throw [NotImplementedException]::new(
        'TODO: lease the current credential generation.'
    )
}

function Invoke-VcfOpsLogCredentialRotation {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [object] $Gate,

        [Parameter(Mandatory)]
        [uri] $BaseUri,

        [Parameter(Mandatory)]
        [string] $LogToken,

        [Parameter(Mandatory)]
        [string] $NewName,

        [ValidateRange(60000, 15552000000)]
        [long] $SessionTtlMilliseconds,

        [ValidateRange(1, 1000)]
        [int] $MaxDrainChecks = 120,

        [ValidateRange(0, 60000)]
        [int] $DrainPollIntervalMilliseconds = 1000,

        [scriptblock] $SleepAction,

        [Net.Http.HttpClient] $HttpClient
    )

    throw [NotImplementedException]::new(
        'TODO: create, validate, cut over, drain, and revoke.'
    )
}

Export-ModuleMember -Function @(
    'New-VcfOpsLogCredentialGate'
    'Get-VcfOpsLogCredentialLease'
    'Invoke-VcfOpsLogCredentialRotation'
)
