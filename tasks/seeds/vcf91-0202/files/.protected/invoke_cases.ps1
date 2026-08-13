[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ModuleManifest,
    [Parameter(Mandatory)] [uri] $BaseUri,
    [Parameter(Mandatory)] [string] $Token,
    [Parameter(Mandatory)] [string] $ScenarioPath,
    [Parameter(Mandatory)] [string] $PassedSddcId,
    [Parameter(Mandatory)] [string] $VcenterPassword,
    [Parameter(Mandatory)] [string] $ExpectedTaskId,
    [Parameter(Mandatory)] [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$PSStyle.OutputRendering = 'PlainText'

Import-Module 'VMware.Sdk.Vcf.Installer' `
    -RequiredVersion '13.5.0.25380678' `
    -Force `
    -ErrorAction Stop
Import-Module $ModuleManifest -Force -ErrorAction Stop
$scenario = Get-Content -LiteralPath $ScenarioPath -Raw | ConvertFrom-Json

function New-ProtectedSddcSpec {
    param([Parameter(Mandatory)] [string] $SddcId)

    $dns = Initialize-VcfInstallerDnsSpec -Subdomain 'lab.example'
    $vcenter = Initialize-VcfInstallerSddcVcenterSpec `
        -VcenterHostname "$SddcId-vc.lab.example" `
        -RootVcenterPassword $VcenterPassword
    $network = Initialize-VcfInstallerSddcNetworkSpec `
        -NetworkType 'MANAGEMENT' `
        -VlanId 0
    $networks = [System.Collections.Generic.List[
        VMware.Bindings.Vcf.Installer.Model.SddcNetworkSpec
    ]]::new()
    $networks.Add($network)
    return Initialize-VcfInstallerSddcSpec `
        -SddcId $SddcId `
        -VcenterSpec $vcenter `
        -NetworkSpecs $networks `
        -DnsSpec $dns
}

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
    $configuration.AccessToken = $Token
    $exceptionFactory =
        [VMware.Binding.OpenApi.Client.Configuration]::DefaultExceptionFactory
    $user = 'protected-' + [guid]::NewGuid().ToString('N')
    $emptyPassword = [Security.SecureString]::new()
    $client = [VMware.Sdk.Vcf.Installer.VcfInstallerServerViClient]::new(
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
        'VCF Installer',
        'VcfInstallerServer'
    )
    foreach ($setting in @(
        @('SessionSecret', $Token),
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

    $server = [VMware.Sdk.Vcf.Installer.VcfInstallerServerImpl]::new(
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

    $passedSpec = New-ProtectedSddcSpec -SddcId $PassedSddcId
    $failureTypes = @(
        foreach ($rejectedCase in $scenario.rejectedCases) {
            $rejectedSpec = New-ProtectedSddcSpec `
                -SddcId ([string] $rejectedCase.spec.sddcId)
            try {
                $unexpected = @(
                    Start-VcfInstallerValidatedSddcDeployment `
                        -Server $server `
                        -SddcSpec $rejectedSpec `
                        -ErrorAction Stop
                )
                throw (
                    "Rejected validation '$($rejectedCase.name)' returned " +
                    "$($unexpected.Count) success objects."
                )
            }
            catch {
                $failureType = $_.Exception.GetType().FullName
                if ($failureType -cne 'System.InvalidOperationException') {
                    throw
                }
                $failureType
            }
        }
    )

    $successOutput = @(
        Start-VcfInstallerValidatedSddcDeployment `
            -Server $server `
            -SddcSpec $passedSpec `
            -ErrorAction Stop
    )
    if ($successOutput.Count -ne 1) {
        throw "Successful deployment returned $($successOutput.Count) objects."
    }
    $task = $successOutput[0]
    if ([string] $task.Id -cne $ExpectedTaskId) {
        throw 'The deployment returned an unexpected task.'
    }

    $output = [ordered] @{
        rejectedPrecheckExceptionTypes = $failureTypes
        successObjectCount = $successOutput.Count
        taskId = [string] $task.Id
        taskName = [string] $task.Name
        taskStatus = [string] $task.Status
        connectionType = $server.GetType().FullName
    }
    $json = $output | ConvertTo-Json -Depth 8 -Compress
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
