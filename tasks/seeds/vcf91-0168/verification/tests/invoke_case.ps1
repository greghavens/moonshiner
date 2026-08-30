[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [int] $Port,

    [Parameter(Mandatory)]
    [string] $ConfigPath,

    [Parameter(Mandatory)]
    [string] $OutputPath,

    [Parameter(Mandatory)]
    [ValidateSet('Retry', 'Drift')]
    [string] $Mode
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

# Import the implementation directly so authoring remains verifiable even
# though the task runner, rather than this shell, provisions VCF PowerCLI.
Import-Module $ModulePath -Force -ErrorAction Stop

$Handler = [Net.Http.HttpClientHandler]::new()
$Handler.AllowAutoRedirect = $false
$HttpClient = [Net.Http.HttpClient]::new($Handler, $false)
try {
    $Arguments = @{
        BaseUri = [uri] "http://127.0.0.1:${Port}/"
        LogToken = $Config.log_token
        Name = $Config.desired.name
        Host = $Config.desired.host
        Port = [int] $Config.desired.port
        Protocol = $Config.desired.protocol
        TransportProtocol = $Config.desired.transportProtocol
        SslEnabled = [bool] $Config.desired.sslEnabled
        Enabled = [bool] $Config.desired.enabled
        HttpClient = $HttpClient
    }

    if ($Mode -ceq 'Retry') {
        $First = Ensure-VcfOpsLogForwarder @Arguments
        $Second = Ensure-VcfOpsLogForwarder @Arguments
        [ordered] @{
            first = $First
            second = $Second
        } |
            ConvertTo-Json -Depth 20 -Compress |
            Set-Content `
                -LiteralPath $OutputPath `
                -Encoding utf8NoBOM `
                -NoNewline
    }
    else {
        $ErrorText = $null
        try {
            $null = Ensure-VcfOpsLogForwarder @Arguments
        }
        catch {
            $ErrorText = $_.Exception.Message
        }
        [ordered] @{
            threw = $null -ne $ErrorText
            error = $ErrorText
        } |
            ConvertTo-Json -Depth 20 -Compress |
            Set-Content `
                -LiteralPath $OutputPath `
                -Encoding utf8NoBOM `
                -NoNewline
    }
}
finally {
    $HttpClient.Dispose()
    $Handler.Dispose()
}
