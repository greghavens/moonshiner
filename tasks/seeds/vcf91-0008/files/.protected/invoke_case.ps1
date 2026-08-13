[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ModuleManifest,
    [Parameter(Mandatory)] [uri] $BaseUri,
    [Parameter(Mandatory)] [string] $AccessToken,
    [Parameter(Mandatory)] [string] $TaskIdOne,
    [Parameter(Mandatory)] [string] $TaskIdTwo,
    [Parameter(Mandatory)] [string] $TaskIdThree,
    [Parameter(Mandatory)] [string] $TaskIdFour,
    [Parameter(Mandatory)] [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$PSStyle.OutputRendering = 'PlainText'

function Assert-RejectedBeforeRequest {
    param(
        [Parameter(Mandatory)] [scriptblock] $Invocation,
        [Parameter(Mandatory)] [string] $CaseName
    )

    $rejected = $false
    try {
        & $Invocation | Out-Null
    } catch {
        $rejected = $true
    }
    if (-not $rejected) {
        throw "The $CaseName pre-request validation case was accepted."
    }
}

Import-Module 'VMware.Sdk.Vcf.SddcManager' `
    -RequiredVersion '13.5.0.25380678' `
    -Force `
    -ErrorAction Stop
Import-Module $ModuleManifest -Force -ErrorAction Stop

$handler = [Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $false
$handler.UseProxy = $false
$httpClient = [Net.Http.HttpClient]::new($handler, $false)
$httpClient.BaseAddress = $BaseUri
$registry = $null
$registryClients = $null
$server = $null
try {
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
        @('Version', '9.1.0.0'),
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

    Assert-RejectedBeforeRequest -CaseName 'null server' -Invocation {
        Get-VcfFailureEvidence -Server $null -TaskId $TaskIdOne
    }
    Assert-RejectedBeforeRequest -CaseName 'blank task id' -Invocation {
        Get-VcfFailureEvidence -Server $server -TaskId '  '
    }
    Assert-RejectedBeforeRequest -CaseName 'poll interval range' -Invocation {
        Get-VcfFailureEvidence `
            -Server $server `
            -TaskId $TaskIdOne `
            -PollIntervalSeconds 61
    }
    Assert-RejectedBeforeRequest -CaseName 'timeout range' -Invocation {
        Get-VcfFailureEvidence `
            -Server $server `
            -TaskId $TaskIdOne `
            -TimeoutSeconds 0
    }

    $results = @(
        foreach ($taskId in @(
            $TaskIdOne,
            $TaskIdTwo,
            $TaskIdThree,
            $TaskIdFour
        )) {
            Get-VcfFailureEvidence `
                -Server $server `
                -TaskId $taskId `
                -PollIntervalSeconds 0 `
                -TimeoutSeconds 10 `
                -ErrorAction Stop
        }
    )
    $projected = @(
        foreach ($result in $results) {
            [ordered] @{
                propertyOrder       = ($result.PSObject.Properties.Name -join ',')
                taskId              = [string] $result.taskId
                taskName            = [string] $result.taskName
                failedSubTask       = [string] $result.failedSubTask
                failedStage         = [string] $result.failedStage
                errorCode           = [string] $result.errorCode
                errorMessage        = [string] $result.errorMessage
                remediationMessage  = [string] $result.remediationMessage
                referenceToken      = [string] $result.referenceToken
                resourceId          = [string] $result.resourceId
                resourceType        = [string] $result.resourceType
                resourceName        = [string] $result.resourceName
                warningCode         = [string] $result.warningCode
                warningMessage      = [string] $result.warningMessage
                supportBundleId     = [string] $result.supportBundleId
                supportBundleName   = [string] $result.supportBundleName
                supportBundleStatus = [string] $result.supportBundleStatus
                logSelection        = [string] $result.logSelection
            }
        }
    )
    $output = [ordered] @{
        resultCount    = $results.Count
        results        = $projected
        preflightRejections = 4
        connectionType = $server.GetType().FullName
        tokenUnchanged = ([string] $server.SessionSecret -ceq $AccessToken)
    }
    $json = $output | ConvertTo-Json -Depth 12 -Compress
    [IO.File]::WriteAllText(
        $OutputPath,
        $json,
        [Text.UTF8Encoding]::new($false)
    )
} finally {
    if ($null -ne $registryClients -and $null -ne $server) {
        $removedClient = $null
        $null = $registryClients.TryRemove($server.Id, [ref] $removedClient)
    }
    $httpClient.Dispose()
    $handler.Dispose()
}
