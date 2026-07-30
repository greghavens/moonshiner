Set-StrictMode -Version Latest

function New-VcfVksNamespaceSession {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        $Server,

        [Parameter(Mandatory)]
        [string] $AccessToken,

        [Parameter(Mandatory)]
        [scriptblock] $RefreshConnection,

        [scriptblock] $OperationInvoker,

        [Net.Http.HttpClient] $HttpClient
    )

    throw 'TODO: create a resumable VKS namespace inventory session.'
}

function Get-VcfVksClusterInventory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        $Session
    )

    throw 'TODO: preserve completed namespace work when the token expires.'
}

Export-ModuleMember -Function @(
    'New-VcfVksNamespaceSession',
    'Get-VcfVksClusterInventory'
)
