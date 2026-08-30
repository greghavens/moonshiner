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

$modulePath = Join-Path (Split-Path -Parent $ModuleManifest) `
    'VcfHostOnboarding.psm1'
$sourceTokens = $null
$sourceParseErrors = $null
$sourceAst = [Management.Automation.Language.Parser]::ParseFile(
    $modulePath, [ref] $sourceTokens, [ref] $sourceParseErrors)
$sourceCommands = @(
    $sourceAst.FindAll({
            param($node)
            $node -is [Management.Automation.Language.CommandAst]
        }, $true) |
        ForEach-Object { $_.GetCommandName() } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Sort-Object -Unique
)
$sourceTypes = @(
    $sourceAst.FindAll({
            param($node)
            $node -is [Management.Automation.Language.TypeExpressionAst] -or
                $node -is [Management.Automation.Language.TypeConstraintAst]
        }, $true) |
        ForEach-Object { $_.TypeName.FullName } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Sort-Object -Unique
)
$sourceFunctions = @(
    $sourceAst.FindAll({
            param($node)
            $node -is [Management.Automation.Language.FunctionDefinitionAst]
        }, $true) |
        ForEach-Object { $_.Name } |
        Sort-Object -Unique
)

Import-Module -Name 'VMware.Sdk.Vcf.SddcManager' `
    -RequiredVersion '13.5.0.25380678' `
    -Force `
    -ErrorAction Stop
Import-Module -Name $ModuleManifest -Force -ErrorAction Stop

$exported = @(
    (Get-Module -Name 'VcfHostOnboarding').ExportedFunctions.Keys
)

function New-CallerOwnedServer {
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
$guardResults = @()

foreach ($case in $cases) {
    $connection = New-CallerOwnedServer -BaseUri ([uri] $case.baseUri) `
        -AccessToken ([string] $case.accessToken)

    $guardProperty = $case.PSObject.Properties['guardChecks']
    if ($null -ne $guardProperty -and $null -ne $guardProperty.Value) {
        foreach ($guard in @($guardProperty.Value)) {
            $guardRecord = [ordered] @{
                name        = [string] $guard.name
                failed      = $false
                errorType   = ''
                outputCount = 0
            }
            try {
                $guardServer = $connection.Server
                if ($guard.PSObject.Properties['nullServer'] -and
                    [bool] $guard.nullServer) {
                    $guardServer = $null
                }
                $guardPollInterval = 0
                $guardTimeout = 1
                if ($guard.PSObject.Properties['pollIntervalSeconds']) {
                    $guardPollInterval = [int] $guard.pollIntervalSeconds
                }
                if ($guard.PSObject.Properties['timeoutSeconds']) {
                    $guardTimeout = [int] $guard.timeoutSeconds
                }
                $guardOutput = [Collections.Generic.List[object]]::new()
                Invoke-VcfHostOnboarding `
                        -Server $guardServer `
                        -PlanPath ([string] $guard.planPath) `
                        -PollIntervalSeconds $guardPollInterval `
                        -TimeoutSeconds $guardTimeout `
                        -ErrorAction Stop |
                    ForEach-Object { $guardOutput.Add($_) }
                $guardRecord.outputCount = $guardOutput.Count
            } catch {
                $guardRecord.outputCount = $guardOutput.Count
                $guardRecord.failed = $true
                $guardRecord.errorType = $_.Exception.GetType().FullName
            }
            $guardResults += $guardRecord
        }
    }

    $record = [ordered] @{
        name                 = [string] $case.name
        failed               = $false
        errorMessage         = ''
        errorType            = ''
        outputCount          = 0
        propertyOrder        = ''
        reportType           = ''
        report               = $null
        serverIntact         = $true
    }
    $pollInterval = 0
    $timeout = 20
    if ($case.PSObject.Properties['pollIntervalSeconds']) {
        $pollInterval = [int] $case.pollIntervalSeconds
    }
    if ($case.PSObject.Properties['timeoutSeconds']) {
        $timeout = [int] $case.timeoutSeconds
    }
    $emitted = [Collections.Generic.List[object]]::new()
    try {
        Invoke-VcfHostOnboarding `
                -Server $connection.Server `
                -PlanPath ([string] $case.planPath) `
                -PollIntervalSeconds $pollInterval `
                -TimeoutSeconds $timeout `
                -ErrorAction Stop |
            ForEach-Object { $emitted.Add($_) }
        $record.outputCount = $emitted.Count
        if ($emitted.Count -eq 1 -and $null -ne $emitted[0]) {
            $report = $emitted[0]
            $record.reportType = $report.GetType().FullName
            $record.propertyOrder =
                ($report.PSObject.Properties.Name -join ',')
            $record.report = $report
        }
    } catch {
        $record.outputCount = $emitted.Count
        $record.failed = $true
        $record.errorMessage = [string] $_.Exception.Message
        $record.errorType = $_.Exception.GetType().FullName
    } finally {
        $clientUsable = $false
        $probeRequest = $null
        $probeResponse = $null
        $probeCancellation = $null
        try {
            $probeRequest = [Net.Http.HttpRequestMessage]::new(
                [Net.Http.HttpMethod]::Head,
                [uri]::new(([uri] $case.baseUri), '__connection_probe__')
            )
            $probeCancellation = [Threading.CancellationTokenSource]::new()
            $probeCancellation.CancelAfter(2000)
            $probeResponse = $connection.HttpClient.SendAsync(
                $probeRequest, $probeCancellation.Token).GetAwaiter().GetResult()
            $clientUsable = $true
        } catch {
            $clientUsable = $false
        } finally {
            if ($null -ne $probeResponse) { $probeResponse.Dispose() }
            if ($null -ne $probeRequest) { $probeRequest.Dispose() }
            if ($null -ne $probeCancellation) { $probeCancellation.Dispose() }
        }
        $record.serverIntact = (
            [string] $connection.Server.SessionSecret -ceq
                [string] $case.accessToken -and
            $connection.Server.IsConnected -and
            $connection.RegistryClients.ContainsKey($connection.Server.Id) -and
            $clientUsable -and
            $connection.HttpClient.BaseAddress -eq ([uri] $case.baseUri)
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
    sourceParseErrors = @($sourceParseErrors | ForEach-Object { $_.ToString() })
    sourceCommands    = @($sourceCommands)
    sourceTypes       = @($sourceTypes)
    sourceFunctions   = @($sourceFunctions)
    guards            = @($guardResults)
    cases             = @($results)
}
$json = $output | ConvertTo-Json -Depth 12 -Compress
[IO.File]::WriteAllText($OutputPath, $json, [Text.UTF8Encoding]::new($false))
