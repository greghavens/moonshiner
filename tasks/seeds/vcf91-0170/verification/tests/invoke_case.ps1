[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [int] $Port,

    [Parameter(Mandatory)]
    [string] $ConfigPath,

    [Parameter(Mandatory)]
    [string] $OutputPath
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

# Import the implementation directly. The task runner provisions the genuine
# external VCF PowerCLI prerequisite declared by the protected manifest.
Import-Module $ModulePath -Force -ErrorAction Stop

$Handler = [Net.Http.HttpClientHandler]::new()
$Handler.AllowAutoRedirect = $false
$HttpClient = [Net.Http.HttpClient]::new($Handler, $false)
try {
    try {
        $Result = New-VcfOpsLogForwarderIfAbsent `
            -BaseUri ([uri] "http://127.0.0.1:${Port}/") `
            -LogToken $Config.log_token `
            -Name $Config.name `
            -Host $Config.host `
            -Port $Config.port `
            -Protocol $Config.protocol `
            -SslEnabled $Config.ssl_enabled `
            -TransportProtocol $Config.transport_protocol `
            -Enabled $Config.enabled `
            -HttpClient $HttpClient

        $Envelope = [ordered] @{
            ok = $true
            result = $Result
        }
    }
    catch {
        $Envelope = [ordered] @{
            ok = $false
            errorType = $_.Exception.GetType().FullName
            message = $_.Exception.Message
        }
    }

    $Envelope |
        ConvertTo-Json -Depth 20 -Compress |
        Set-Content `
            -LiteralPath $OutputPath `
            -Encoding utf8NoBOM `
            -NoNewline
}
finally {
    $HttpClient.Dispose()
    $Handler.Dispose()
}
