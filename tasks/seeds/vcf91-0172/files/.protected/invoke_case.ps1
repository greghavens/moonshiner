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
    Join-Path $FilesRoot 'VcfOpsLogDiagnosis'
) 'VcfOpsLogDiagnosis.psm1'
$Config = Get-Content -LiteralPath $ConfigPath -Raw |
    ConvertFrom-Json -AsHashtable

# Import the implementation file directly so author verification remains
# possible when the task runner, rather than this shell, provisions the
# protected manifest's external VCF PowerCLI prerequisite.
Import-Module $ModulePath -Force -ErrorAction Stop

$Handler = [Net.Http.HttpClientHandler]::new()
$Handler.AllowAutoRedirect = $false
$HttpClient = [Net.Http.HttpClient]::new($Handler, $false)
try {
    $RejectedBeforeTraffic = $false
    try {
        $null = Get-VcfOpsIncidentDiagnosis `
            -BaseUri ([uri] "http://127.0.0.1:${Port}/") `
            -LogToken $Config.log_token `
            -RequestId $Config.request_id `
            -StartTimeMillis -1 `
            -EndTimeMillis 0 `
            -HttpClient $HttpClient
    }
    catch [ArgumentOutOfRangeException] {
        $RejectedBeforeTraffic = $true
    }
    if (-not $RejectedBeforeTraffic) {
        throw 'Invalid time bounds were not rejected before traffic.'
    }

    $Result = Get-VcfOpsIncidentDiagnosis `
        -BaseUri ([uri] "http://127.0.0.1:${Port}/") `
        -LogToken $Config.log_token `
        -RequestId $Config.request_id `
        -StartTimeMillis $Config.start_time `
        -EndTimeMillis $Config.end_time `
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
