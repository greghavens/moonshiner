[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ModuleManifest,
    [Parameter(Mandatory)] [string] $CasesPath,
    [Parameter(Mandatory)] [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$PSStyle.OutputRendering = 'PlainText'

Import-Module -Name 'VMware.Sdk.Vcf.SddcManager' `
    -RequiredVersion '13.5.0.25380678' `
    -Force `
    -ErrorAction Stop
Import-Module -Name $ModuleManifest -Force -ErrorAction Stop

$exported = @(
    (Get-Module -Name 'VcfFailureTriage').ExportedFunctions.Keys
)

function New-CallerOwnedServer {
    <#
        .SYNOPSIS
        Builds a genuine SDK connection object aimed at one loopback
        instance.  The caller owns it: the module under test must not
        connect, disconnect, mutate or dispose it.
    #>
    param(
        [Parameter(Mandatory)] [uri] $BaseUri,
        [Parameter(Mandatory)] [string] $AccessToken
    )

    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    $handler.UseProxy = $false
    $httpClient = [Net.Http.HttpClient]::new($handler, $false)
    $httpClient.BaseAddress = $BaseUri

    $configuration = [VMware.Binding.OpenApi.Client.Configuration]::new()
    $configuration.BasePath = [string] $BaseUri
    $configuration.AccessToken = $AccessToken
    $exceptionFactory =
        [VMware.Binding.OpenApi.Client.Configuration]::DefaultExceptionFactory
    $user = 'protected-' + [guid]::NewGuid().ToString('N')
    $emptyPassword = [Security.SecureString]::new()

    $client = [VMware.Sdk.Vcf.SddcManager.VcfSddcManagerViClient]::new(
        $httpClient,
        $BaseUri,
        $user,
        $emptyPassword,
        $configuration,
        $exceptionFactory
    )
    $internal = [VMware.Sdk.OpenApi.Cmdlets.OpenApiConnectionInternal]::new(
        $httpClient,
        $BaseUri,
        $user,
        $configuration,
        $exceptionFactory,
        'VCF SDDC Manager',
        'VcfSddcManagerServer'
    )
    foreach ($setting in @(
        @('SessionSecret', $AccessToken),
        @('Version', '9.0.0.0'),
        @('IsConnected', $true)
    )) {
        $property = $internal.GetType().GetProperty([string] $setting[0])
        $setter = $property.GetSetMethod($true)
        $null = $setter.Invoke($internal, [object[]] @($setting[1]))
    }
    $internalField = $client.GetType().GetField(
        '_internalConnection',
        [Reflection.BindingFlags] 'NonPublic,Instance'
    )
    $internalField.SetValue($client, $internal)

    $server = [VMware.Sdk.Vcf.SddcManager.VcfSddcManagerServerImpl]::new(
        $BaseUri,
        $user
    )
    $registry =
        [VMware.VimAutomation.Sdk.Interop.V1.CoreServiceFactory]::CoreService.VIClientRegistry
    $registryClients = $registry.GetType().GetField(
        '_clients',
        [Reflection.BindingFlags] 'NonPublic,Instance'
    ).GetValue($registry)
    if (-not $registryClients.TryAdd($server.Id, $client)) {
        throw 'Unable to register the caller-owned genuine SDK connection.'
    }
    if ([string] $server.SessionSecret -cne $AccessToken) {
        throw 'The registered genuine SDK connection did not retain its token.'
    }

    return [pscustomobject] @{
        Server          = $server
        RegistryClients = $registryClients
        HttpClient      = $httpClient
        Handler         = $handler
    }
}

$cases = @(Get-Content -LiteralPath $CasesPath -Raw | ConvertFrom-Json)
$results = @()

foreach ($case in $cases) {
    $connection = New-CallerOwnedServer -BaseUri ([uri] $case.baseUri) `
        -AccessToken ([string] $case.accessToken)
    $caseTimer = [Diagnostics.Stopwatch]::StartNew()
    $record = [ordered] @{
        name          = [string] $case.name
        failed        = $false
        errorMessage  = ''
        errorType     = ''
        outputCount   = 0
        propertyOrder = ''
        report        = $null
        serverIntact  = $true
        elapsedMilliseconds = 0
    }
    try {
        $arguments = @{
            Server              = $connection.Server
            TaskId              = [string] $case.taskId
            PollIntervalSeconds = [int] $case.pollIntervalSeconds
            TimeoutSeconds      = [int] $case.timeoutSeconds
            ErrorAction         = 'Stop'
        }
        if ($case.includeHealthCheck) { $arguments['IncludeHealthCheck'] = $true }
        if ($case.forceCollection) { $arguments['ForceCollection'] = $true }
        switch ([string] $case.mode) {
            'nullServer' { $arguments['Server'] = $null }
            'blankTaskId' { $arguments['TaskId'] = "  `t " }
            default { }
        }
        $emitted = @(Invoke-VcfFailureTriage @arguments)
        $record.outputCount = $emitted.Count
        if ($emitted.Count -eq 1 -and $null -ne $emitted[0]) {
            $report = $emitted[0]
            $record.propertyOrder = ($report.PSObject.Properties.Name -join ',')
            $record.report = $report
        }
    } catch {
        $record.failed = $true
        $record.errorMessage = [string] $_.Exception.Message
        $record.errorType = $_.Exception.GetType().FullName
    } finally {
        $caseTimer.Stop()
        $record.elapsedMilliseconds = [long] $caseTimer.ElapsedMilliseconds
        $record.serverIntact = (
            [string] $connection.Server.SessionSecret -ceq
                [string] $case.accessToken -and
            $connection.Server.IsConnected
        )
        $removed = $null
        $null = $connection.RegistryClients.TryRemove(
            $connection.Server.Id, [ref] $removed)
        $connection.HttpClient.Dispose()
        $connection.Handler.Dispose()
    }
    $results += $record
}

$output = [ordered] @{
    exportedFunctions = @($exported)
    cases             = @($results)
}
$json = $output | ConvertTo-Json -Depth 12 -Compress
[IO.File]::WriteAllText($OutputPath, $json, [Text.UTF8Encoding]::new($false))
