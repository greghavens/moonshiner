[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [int] $Port,

    [Parameter(Mandatory)]
    [string] $OutputFile
)

$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
$manifest = Join-Path $root 'src/VcfInstaller.Proxy.psd1'
$sdkVersion = [version] '13.5.0.25380678'

$installed = Get-Module -ListAvailable -Name VMware.Sdk.Vcf.Installer |
    Where-Object Version -EQ $sdkVersion |
    Select-Object -First 1
if ($null -eq $installed) {
    throw 'Required environment prerequisite VMware.Sdk.Vcf.Installer 13.5.0.25380678 is not installed.'
}

Import-Module $manifest -Force

foreach ($sdkCommandName in @(
    'Initialize-VcfInstallerProxyConfiguration',
    'Invoke-VcfInstallerUpdateProxyConfiguration',
    'Invoke-VcfInstallerGetTask'
)) {
    $sdkCommand = Get-Command $sdkCommandName -CommandType Cmdlet -ErrorAction Stop
    if ($sdkCommand.Source -cne 'VMware.Sdk.Vcf.Installer') {
        throw "$sdkCommandName did not resolve to the genuine Installer SDK module."
    }
}

$candidate = Get-Command Set-VcfInstallerProxyConfiguration `
    -CommandType Function -ErrorAction Stop
$candidateExports = @(Get-Command -Module $candidate.ModuleName -CommandType Function)
if ($candidateExports.Count -ne 1 -or
    $candidateExports[0].Name -cne 'Set-VcfInstallerProxyConfiguration') {
    throw 'The candidate module must export exactly Set-VcfInstallerProxyConfiguration.'
}

$expectedTypes = [ordered]@{
    Server = [object]
    IsEnabled = [bool]
    ProxyHost = [string]
    Port = [int]
    TransferProtocol = [string]
    Username = [string]
    Password = [string]
    IsAuthenticated = [bool]
    PollIntervalSeconds = [int]
    TimeoutSeconds = [int]
}
foreach ($entry in $expectedTypes.GetEnumerator()) {
    $parameter = $candidate.Parameters[$entry.Key]
    if ($null -eq $parameter -or $parameter.ParameterType -ne $entry.Value) {
        throw "Public parameter $($entry.Key) is missing or has the wrong type."
    }
    $mandatory = @(
        $parameter.Attributes |
            Where-Object { $_ -is [System.Management.Automation.ParameterAttribute] }
    )[0].Mandatory
    $shouldBeMandatory = $entry.Key -in @('Server', 'IsEnabled')
    if ($mandatory -ne $shouldBeMandatory) {
        throw "Public parameter $($entry.Key) has the wrong Mandatory contract."
    }
}
if (@($candidate.Parameters.ProxyHost.Aliases) -cnotcontains 'Host') {
    throw 'ProxyHost must retain its caller-facing Host alias.'
}

$portRange = @(
    $candidate.Parameters.Port.Attributes |
        Where-Object { $_ -is [System.Management.Automation.ValidateRangeAttribute] }
)
$pollRange = @(
    $candidate.Parameters.PollIntervalSeconds.Attributes |
        Where-Object { $_ -is [System.Management.Automation.ValidateRangeAttribute] }
)
$timeoutRange = @(
    $candidate.Parameters.TimeoutSeconds.Attributes |
        Where-Object { $_ -is [System.Management.Automation.ValidateRangeAttribute] }
)
$protocolSet = @(
    $candidate.Parameters.TransferProtocol.Attributes |
        Where-Object { $_ -is [System.Management.Automation.ValidateSetAttribute] }
)
if ($portRange.Count -ne 1 -or
    [int] $portRange[0].MinRange -ne 1 -or
    [int] $portRange[0].MaxRange -ne 65535) {
    throw 'Port must retain ValidateRange(1, 65535).'
}
if ($pollRange.Count -ne 1 -or
    [int] $pollRange[0].MinRange -ne 0 -or
    [int] $pollRange[0].MaxRange -ne 3600) {
    throw 'PollIntervalSeconds must retain ValidateRange(0, 3600).'
}
if ($timeoutRange.Count -ne 1 -or
    [int] $timeoutRange[0].MinRange -ne 1 -or
    [int] $timeoutRange[0].MaxRange -ne 86400) {
    throw 'TimeoutSeconds must retain ValidateRange(1, 86400).'
}
if ($protocolSet.Count -ne 1 -or
    (@($protocolSet[0].ValidValues) -join ',') -cne 'HTTP,HTTPS') {
    throw 'TransferProtocol must retain ValidateSet(HTTP, HTTPS).'
}

$defaultText = @{}
foreach ($parameterAst in $candidate.ScriptBlock.Ast.Body.ParamBlock.Parameters) {
    $name = $parameterAst.Name.VariablePath.UserPath
    $defaultText[$name] = if ($null -eq $parameterAst.DefaultValue) {
        ''
    }
    else {
        $parameterAst.DefaultValue.Extent.Text
    }
}
if ($defaultText.PollIntervalSeconds -cne '2' -or
    $defaultText.TimeoutSeconds -cne '300') {
    throw 'Polling parameter defaults must remain 2 seconds and 300 seconds.'
}

$connectCommand = Get-Command Connect-VcfInstallerServer -ErrorAction Stop
$connect = @{
    User = 'moonshiner-verifier'
}
$plainPassword = 'local-loopback-password'
if ($connectCommand.Parameters.Password.ParameterType -eq [securestring]) {
    $connect.Password = ConvertTo-SecureString $plainPassword -AsPlainText -Force
}
else {
    $connect.Password = $plainPassword
}
if ($connectCommand.Parameters.ContainsKey('Port')) {
    $connect.Port = $Port
}
if ($connectCommand.Parameters.ContainsKey('Protocol')) {
    $connect.Server = '127.0.0.1'
    $connect.Protocol = 'http'
}
else {
    $connect.Server = "http://127.0.0.1:$Port"
}
if ($connectCommand.Parameters.ContainsKey('Force')) {
    $connect.Force = $true
}
if ($connectCommand.Parameters.ContainsKey('NotDefault')) {
    $connect.NotDefault = $true
}
$serverConnection = Connect-VcfInstallerServer @connect

function Invoke-ProxyCase {
    param([hashtable] $Arguments)

    $items = @(Set-VcfInstallerProxyConfiguration `
        -Server $serverConnection @Arguments)
    if ($items.Count -ne 1) {
        throw "Expected exactly one terminal Task object, received $($items.Count)."
    }
    return $items[0]
}

$successful = Invoke-ProxyCase -Arguments @{
    IsEnabled = $true
    Host = 'proxy.example.com'
    Port = 3128
    TransferProtocol = 'HTTPS'
    IsAuthenticated = $false
    PollIntervalSeconds = 0
    TimeoutSeconds = 5
}
$warning = Invoke-ProxyCase -Arguments @{
    IsEnabled = $false
    PollIntervalSeconds = 0
    TimeoutSeconds = 5
}
$failed = Invoke-ProxyCase -Arguments @{
    IsEnabled = $true
    Host = 'authenticated.example.com'
    Port = 8080
    TransferProtocol = 'HTTP'
    Username = 'proxy-user'
    Password = 'proxy-password'
    IsAuthenticated = $true
    PollIntervalSeconds = 0
    TimeoutSeconds = 5
}
$cancelled = Invoke-ProxyCase -Arguments @{
    IsEnabled = $false
    Host = 'cancelled.example.com'
    PollIntervalSeconds = 0
    TimeoutSeconds = 5
}
$skipped = Invoke-ProxyCase -Arguments @{
    IsEnabled = $false
    Host = 'skipped.example.com'
    PollIntervalSeconds = 0
    TimeoutSeconds = 5
}
$timedOutTask = Invoke-ProxyCase -Arguments @{
    IsEnabled = $false
    Host = 'timed-out-task.example.com'
    PollIntervalSeconds = 0
    TimeoutSeconds = 5
}

try {
    $null = Invoke-ProxyCase -Arguments @{
        IsEnabled = $false
        Host = 'unexpected.example.com'
        PollIntervalSeconds = 0
        TimeoutSeconds = 5
    }
    $unexpectedRejected = $false
}
catch {
    $unexpectedRejected = $true
}
if (-not $unexpectedRejected) {
    throw 'An unexpected task status was accepted.'
}

try {
    $null = Invoke-ProxyCase -Arguments @{
        IsEnabled = $false
        Host = 'poll-timeout.example.com'
        PollIntervalSeconds = 0
        TimeoutSeconds = 1
    }
    $timeoutException = 'NO_EXCEPTION'
}
catch {
    $timeoutException = $_.Exception.GetType().FullName
}
if ($timeoutException -cne 'System.TimeoutException') {
    throw "Polling timeout produced $timeoutException instead of System.TimeoutException."
}

[ordered]@{
    firstTask = [ordered]@{
        id = [string] $successful.Id
        name = [string] $successful.Name
        status = [string] $successful.Status
    }
    terminalStatuses = @(
        [string] $successful.Status
        [string] $warning.Status
        [string] $failed.Status
        [string] $cancelled.Status
        [string] $skipped.Status
        [string] $timedOutTask.Status
    )
    unexpectedRejected = $unexpectedRejected
    timeoutException = $timeoutException
} | ConvertTo-Json -Depth 4 -Compress |
    Set-Content -LiteralPath $OutputFile -Encoding utf8NoBOM
