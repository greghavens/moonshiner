Set-StrictMode -Version Latest

function Get-VcfOpsLogAgentGroupInventory {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [uri] $BaseUri,

        [Parameter(Mandatory)]
        [string] $LogToken,

        [ValidateRange(1, 1000)]
        [int] $PageSize = 100,

        [Net.Http.HttpClient] $HttpClient
    )

    throw [NotImplementedException]::new(
        'TODO: retrieve every agent-group page and return stable inventory.'
    )
}

Export-ModuleMember -Function Get-VcfOpsLogAgentGroupInventory
