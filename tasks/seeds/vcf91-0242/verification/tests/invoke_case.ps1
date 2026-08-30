[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ModuleManifest,
    [Parameter(Mandatory)] [string] $CasePath,
    [Parameter(Mandatory)] [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$PSStyle.OutputRendering = 'PlainText'

Import-Module 'VMware.Sdk.Vcf.SddcManager' `
    -RequiredVersion '13.5.0.25380678' `
    -Force `
    -ErrorAction Stop
Import-Module $ModuleManifest -Force -ErrorAction Stop

$case = Get-Content -LiteralPath $CasePath -Raw | ConvertFrom-Json

$securePassword = [Security.SecureString]::new()
foreach ($character in [char[]] $case.password) {
    $securePassword.AppendChar($character)
}
$securePassword.MakeReadOnly()
$credential = [pscredential]::new([string] $case.username, $securePassword)

$handler = [Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $false
$handler.UseProxy = $false
$httpClient = [Net.Http.HttpClient]::new($handler, $false)
$httpClient.DefaultRequestHeaders.Add(
    'X-Moonshiner-Client-Marker',
    [string] $case.clientMarker
)
$apiClient = [VMware.Binding.OpenApi.Client.ApiClient]::new(
    $httpClient,
    [string] $case.basePath
)

try {
    # Exercise the local preconditions before the successful workflow. Any
    # implementation that contacts the appliance for one of these calls leaves
    # an extra entry in the protected request log and fails verification.
    $validArguments = @{
        ApiClient           = $apiClient
        BasePath            = [string] $case.basePath
        Credential          = $credential
        ClusterId           = [string] $case.clusterId
        ProtectionGroupName = [string] $case.protectionGroupName
        SnapshotName        = [string] $case.snapshotName
        VmId                = @('validation-vm')
        PollIntervalSeconds = 0
        TimeoutSeconds      = 20
        ErrorAction         = 'Stop'
    }

    function Assert-LocallyRejected {
        param(
            [Parameter(Mandatory)] [string] $Label,
            [hashtable] $Override = @{},
            [string[]] $Remove = @()
        )

        $attempt = @{}
        foreach ($name in $validArguments.Keys) {
            $attempt[$name] = $validArguments[$name]
        }
        foreach ($name in $Override.Keys) {
            $attempt[$name] = $Override[$name]
        }
        foreach ($name in $Remove) {
            $attempt.Remove($name)
        }

        $rejected = $false
        try {
            New-VsanDpProtectedSnapshot @attempt | Out-Null
        } catch {
            $rejected = $true
        }
        if (-not $rejected) {
            throw "The invalid input case '$Label' was not rejected."
        }
    }

    Assert-LocallyRejected -Label 'null ApiClient' -Override @{ ApiClient = $null }
    Assert-LocallyRejected -Label 'blank BasePath' -Override @{ BasePath = '   ' }
    Assert-LocallyRejected -Label 'null Credential' -Override @{ Credential = $null }
    Assert-LocallyRejected -Label 'blank ClusterId' -Override @{ ClusterId = "`t" }
    Assert-LocallyRejected -Label 'blank ProtectionGroupName' `
        -Override @{ ProtectionGroupName = '  ' }
    Assert-LocallyRejected -Label 'blank SnapshotName' -Override @{ SnapshotName = "`r`n" }
    Assert-LocallyRejected -Label 'missing VM selector' -Remove @('VmId')
    Assert-LocallyRejected -Label 'partial policy' -Override @{ PolicyName = 'incomplete' }
    Assert-LocallyRejected -Label 'partial snapshot retention' `
        -Override @{ SnapshotRetentionUnit = 'HOUR' }

    $arguments = @{
        ApiClient           = $apiClient
        BasePath            = [string] $case.basePath
        Credential          = $credential
        ClusterId           = [string] $case.clusterId
        ProtectionGroupName = [string] $case.protectionGroupName
        SnapshotName        = [string] $case.snapshotName
        PollIntervalSeconds = 0
        TimeoutSeconds      = 20
        ErrorAction         = 'Stop'
    }
    foreach ($name in @(
        'VmNamePattern', 'VmId', 'PolicyName', 'PolicyIntervalUnit', 'PolicyInterval',
        'PolicyRetentionUnit', 'PolicyRetentionDuration',
        'SnapshotRetentionUnit', 'SnapshotRetentionDuration'
    )) {
        $property = $case.PSObject.Properties[$name]
        if ($null -ne $property -and $null -ne $property.Value) {
            $arguments[$name] = $property.Value
        }
    }
    if ([bool] $case.locked) {
        $arguments['Locked'] = [switch]::Present
    }

    $results = @(New-VsanDpProtectedSnapshot @arguments)
    if ($results.Count -ne 1) {
        throw "Expected exactly one object on the success pipeline, saw $($results.Count)."
    }
    $result = $results[0]

    # The caller owns the client. Prove the module left it usable by issuing one
    # more genuine request through it; the deliberately bogus token makes the
    # appliance answer 401 without disturbing any appliance state.
    $probeOptions = [VMware.Binding.OpenApi.Client.RequestOptions]::new()
    $probeOptions.PathParameters['task'] = [string] $result.createTaskId
    $probeOptions.HeaderParameters.Add('vmware-api-session-id', [string] $case.probeToken)
    $probeOptions.HeaderParameters.Add('Accept', 'application/json')
    $probeConfiguration = [VMware.Binding.OpenApi.Client.Configuration]::new()
    $probeConfiguration.BasePath = [string] $case.basePath
    $probeResponse = $apiClient.Get[object](
        '/snapservice/tasks/{task}',
        $probeOptions,
        $probeConfiguration
    )

    $output = [ordered] @{
        propertyOrder = ($result.PSObject.Properties.Name -join ',')
        result        = [ordered] @{
            clusterId             = [string] $result.clusterId
            protectionGroupId     = [string] $result.protectionGroupId
            protectionGroupName   = [string] $result.protectionGroupName
            protectionGroupStatus = [string] $result.protectionGroupStatus
            createTaskId          = [string] $result.createTaskId
            snapshotTaskId        = [string] $result.snapshotTaskId
            snapshotId            = [string] $result.snapshotId
            snapshotName          = [string] $result.snapshotName
            sessionCreateCount    = [int] $result.sessionCreateCount
            tokenRefreshCount     = [int] $result.tokenRefreshCount
        }
        apiClientType = $apiClient.GetType().FullName
        probeStatus   = [string] $probeResponse.StatusCode
    }
    $json = $output | ConvertTo-Json -Depth 12 -Compress
    [IO.File]::WriteAllText($OutputPath, $json, [Text.UTF8Encoding]::new($false))
} finally {
    $apiClient.Dispose()
    $handler.Dispose()
}
