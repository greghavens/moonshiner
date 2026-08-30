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
    Join-Path $FilesRoot 'VcfOpsLogRouting'
) 'VcfOpsLogRouting.psm1'
$Config = Get-Content -LiteralPath $ConfigPath -Raw |
    ConvertFrom-Json -AsHashtable

# Import the implementation file directly so seed authoring remains verifiable
# even when the task runner, rather than this authoring shell, provisions the
# protected manifest's external VCF PowerCLI prerequisite.
Import-Module $ModulePath -Force -ErrorAction Stop

$Handler = [Net.Http.HttpClientHandler]::new()
$Handler.AllowAutoRedirect = $false
$HttpClient = [Net.Http.HttpClient]::new($Handler, $false)
try {
    $Result = Invoke-VcfOpsLogRoutingChange `
        -BaseUri ([uri] "http://127.0.0.1:${Port}/") `
        -LogToken $Config.log_token `
        -AgentGroupId $Config.agent_group_id `
        -AgentAutoUpdate $Config.agent_auto_update `
        -ForwarderId $Config.forwarder_id `
        -ForwarderEnabled $Config.forwarder_enabled `
        -TestHost $Config.test_host `
        -TestPort $Config.test_port `
        -TestProtocol $Config.test_protocol `
        -TestSslEnabled $Config.test_ssl_enabled `
        -TestTransportProtocol $Config.test_transport_protocol `
        -HttpClient $HttpClient

    $Result |
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
