param(
    [Parameter(Mandatory)]
    [uri] $Server,

    [Parameter(Mandatory)]
    [string] $ResultPath,

    [switch] $ShowDetails,

    [ValidateSet('Local', 'ActiveDirectory', 'vIDM')]
    [string] $Provider = 'Local',

    [switch] $Pipeline
)

$ErrorActionPreference = 'Stop'
$modulePath = Join-Path $PSScriptRoot '../src/VcfOperationsForLogs.psd1'
Import-Module $modulePath -Force

$command = Get-Command Set-VcfLogForwarders -ErrorAction Stop
$expectedTypes = [ordered]@{
    Server = [uri]
    Credential = [pscredential]
    Forwarder = [object[]]
    Provider = [string]
    ShowDetails = [switch]
}
foreach ($entry in $expectedTypes.GetEnumerator()) {
    if ($command.Parameters[$entry.Key].ParameterType -ne $entry.Value) {
        throw "Parameter $($entry.Key) no longer has the scaffolded type."
    }
}
foreach ($name in 'Server', 'Credential', 'Forwarder') {
    $mandatory = @(
        $command.Parameters[$name].Attributes |
            Where-Object { $_ -is [System.Management.Automation.ParameterAttribute] -and $_.Mandatory }
    )
    if ($mandatory.Count -eq 0) {
        throw "Parameter $name is no longer mandatory."
    }
}
$pipelineBinding = @(
    $command.Parameters.Forwarder.Attributes |
        Where-Object {
            $_ -is [System.Management.Automation.ParameterAttribute] -and $_.ValueFromPipeline
        }
)
if ($pipelineBinding.Count -eq 0) {
    throw 'Forwarder no longer accepts pipeline input.'
}

$securePassword = ConvertTo-SecureString 'fixture-password' -AsPlainText -Force
$credential = [pscredential]::new('ops-admin', $securePassword)
$forwarders = @(
    [pscustomobject]@{
        Name = 'edge-syslog'
        Host = '192.0.2.10'
        Port = 514
        Protocol = 'SYSLOG'
        SslEnabled = $false
        WorkerCount = $null
        Filter = $null
    },
    [pscustomobject]@{
        Name = 'cfapi-archive'
        Host = '192.0.2.20'
        Port = 9000
        Protocol = 'CFAPI'
        SslEnabled = $true
        AcceptCert = $false
        WorkerCount = 6
        ConnectionRefreshInterval = 30
        DiskCacheSize = 0
        Tags = @{ site = 'central' }
        Filter = 'severity:error'
        TransportProtocol = 'TCP_OCTET'
        ForwardComplementaryFields = $false
        TestConnection = $true
    }
)

$parameters = @{
    Server = $Server
    Credential = $credential
    Provider = $Provider
}
if ($ShowDetails.IsPresent) {
    $parameters.ShowDetails = $true
}
if ($Pipeline.IsPresent) {
    $result = @($forwarders | Set-VcfLogForwarders @parameters)
}
else {
    $parameters.Forwarder = $forwarders
    $result = @(Set-VcfLogForwarders @parameters)
}
$result | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $ResultPath -Encoding utf8NoBOM
