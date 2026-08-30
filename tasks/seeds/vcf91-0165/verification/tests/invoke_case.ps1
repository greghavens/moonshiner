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
    Join-Path $FilesRoot 'VcfOpsLogSecret'
) 'VcfOpsLogSecret.psm1'
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
    $Result = New-VcfOpsLogAgentSecretAndWait `
        -BaseUri ([uri] "http://127.0.0.1:${Port}/") `
        -LogToken $Config.log_token `
        -Name $Config.secret_name `
        -PageSize $Config.page_size `
        -MaxPolls $Config.max_polls `
        -PollIntervalMilliseconds 0 `
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
