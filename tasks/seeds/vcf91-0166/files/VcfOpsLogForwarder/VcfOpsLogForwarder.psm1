Set-StrictMode -Version Latest

function Sync-VcfOpsLogForwarder {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [uri] $BaseUri,

        [Parameter(Mandatory)]
        [scriptblock] $AccessTokenProvider,

        [Parameter(Mandatory)]
        [Collections.IDictionary[]] $DesiredForwarders,

        [Net.Http.HttpClient] $HttpClient
    )

    throw [NotImplementedException]::new(
        'TODO: reconcile forwarders and refresh only the interrupted request.'
    )
}

Export-ModuleMember -Function Sync-VcfOpsLogForwarder
