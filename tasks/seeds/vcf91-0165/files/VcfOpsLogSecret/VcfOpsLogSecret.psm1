Set-StrictMode -Version Latest

function New-VcfOpsLogAgentSecretAndWait {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [uri] $BaseUri,

        [Parameter(Mandatory)]
        [string] $LogToken,

        [Parameter(Mandatory)]
        [string] $Name,

        [ValidateRange(1, 1000)]
        [int] $PageSize = 100,

        [ValidateRange(1, 100)]
        [int] $MaxPolls = 20,

        [ValidateRange(0, 60000)]
        [int] $PollIntervalMilliseconds = 1000,

        [Net.Http.HttpClient] $HttpClient
    )

    throw [NotImplementedException]::new(
        'TODO: create the agent secret and poll activation to a terminal state.'
    )
}

Export-ModuleMember -Function New-VcfOpsLogAgentSecretAndWait
