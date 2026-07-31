Set-StrictMode -Version Latest

function Get-VcfOpsIncidentDiagnosis {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [uri] $BaseUri,

        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string] $LogToken,

        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string] $RequestId,

        [Parameter(Mandatory)]
        [long] $StartTimeMillis,

        [Parameter(Mandatory)]
        [long] $EndTimeMillis,

        [Net.Http.HttpClient] $HttpClient
    )

    throw [NotImplementedException]::new(
        'TODO: correlate the failure log with its platform event.'
    )
}

Export-ModuleMember -Function Get-VcfOpsIncidentDiagnosis
