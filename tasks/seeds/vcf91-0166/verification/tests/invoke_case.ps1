[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [int] $Port,

    [Parameter(Mandatory)]
    [string] $ConfigPath,

    [Parameter(Mandatory)]
    [string] $OutputPath,

    [string] $SocketPath = '',

    [string] $FallbackContractPath = '',

    [string] $FallbackLogPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$FilesRoot = Split-Path -Parent $PSScriptRoot
$ModulePath = Join-Path (
    Join-Path $FilesRoot 'VcfOpsLogForwarder'
) 'VcfOpsLogForwarder.psm1'
$Config = Get-Content -LiteralPath $ConfigPath -Raw |
    ConvertFrom-Json -AsHashtable

# Import the implementation directly: the selected runner provisions the
# protected manifest's external VCF PowerCLI prerequisite, while the authoring
# shell need not have that external module installed.
Import-Module $ModulePath -Force -ErrorAction Stop

$ProviderCalls = [Collections.Generic.List[bool]]::new()
$Provider = {
    param([bool] $ForceRefresh)
    $ProviderCalls.Add($ForceRefresh)
    if ($ForceRefresh) {
        return $Config.new_token
    }
    return $Config.old_token
}.GetNewClosure()

$Handler = $null
if (-not [string]::IsNullOrEmpty($FallbackContractPath)) {
    $FallbackSource = Join-Path $PSScriptRoot 'FallbackHandler.cs'
    Add-Type -Path $FallbackSource
    $Handler = [VcfContractFallbackHandler]::new(
        $FallbackContractPath,
        $ConfigPath,
        $FallbackLogPath
    )
}
elseif ([string]::IsNullOrEmpty($SocketPath)) {
    $Handler = [Net.Http.HttpClientHandler]::new()
    $Handler.AllowAutoRedirect = $false
}
else {
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Net.Http;
using System.Net.Sockets;
using System.Threading;
using System.Threading.Tasks;

public static class VcfUnixHttpClientFactory
{
    public static SocketsHttpHandler Create(string path)
    {
        var handler = new SocketsHttpHandler();
        handler.AllowAutoRedirect = false;
        handler.ConnectCallback = (context, cancellationToken) =>
            Connect(path, cancellationToken);
        return handler;
    }

    private static async ValueTask<Stream> Connect(
        string path,
        CancellationToken cancellationToken)
    {
        var socket = new Socket(
            AddressFamily.Unix,
            SocketType.Stream,
            ProtocolType.Unspecified);
        try
        {
            await socket.ConnectAsync(
                new UnixDomainSocketEndPoint(path),
                cancellationToken).ConfigureAwait(false);
            return new NetworkStream(socket, ownsSocket: true);
        }
        catch
        {
            socket.Dispose();
            throw;
        }
    }
}
'@
    $Handler = [VcfUnixHttpClientFactory]::Create($SocketPath)
}
$HttpClient = [Net.Http.HttpClient]::new($Handler, $false)
try {
    $Result = Sync-VcfOpsLogForwarder `
        -BaseUri ([uri] "http://127.0.0.1:${Port}/") `
        -AccessTokenProvider $Provider `
        -DesiredForwarders ([Collections.IDictionary[]] $Config.desired) `
        -HttpClient $HttpClient

    [pscustomobject] [ordered] @{
        Result = $Result
        ProviderCalls = [bool[]] $ProviderCalls.ToArray()
    } |
        ConvertTo-Json -Depth 50 -Compress |
        Set-Content `
            -LiteralPath $OutputPath `
            -Encoding utf8NoBOM `
            -NoNewline
}
finally {
    $HttpClient.Dispose()
    $Handler.Dispose()
}
