$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $Root (
    'VcfVcenterCredentialGate/VcfVcenterCredentialGate.psd1'
)
$ModulePath = Join-Path $Root (
    'VcfVcenterCredentialGate/VcfVcenterCredentialGate.psm1'
)
$ContractPath = Join-Path $Root 'docs/contract.json'
$SourcesPath = Join-Path $Root 'docs/official_sources.json'
$MockPath = Join-Path $PSScriptRoot 'mock_vcenter.py'
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'vcf91-0095-' + [guid]::NewGuid().ToString('N')
)
[IO.Directory]::CreateDirectory($TempRoot) | Out-Null
$PortPath = Join-Path $TempRoot 'port.txt'
$LogPath = Join-Path $TempRoot 'requests.jsonl'
$ScenarioPath = Join-Path $TempRoot 'scenario.json'
$ReleasePath = Join-Path $TempRoot 'release-old-request'
$StdoutPath = Join-Path $TempRoot 'mock.stdout'
$StderrPath = Join-Path $TempRoot 'mock.stderr'
$MockProcess = $null
$OldInvocation = $null
$RotationInvocation = $null
$QueuedInvocation = $null
$QueuedAttempting = $null

function Assert-True {
    param(
        [bool] $Condition,
        [Parameter(Mandatory)]
        [string] $Message
    )
    if (-not $Condition) {
        throw "ASSERTION FAILED: $Message"
    }
}

function Assert-Equal {
    param(
        [AllowNull()]
        $Actual,
        [AllowNull()]
        $Expected,
        [Parameter(Mandatory)]
        [string] $Message
    )
    if ($Actual -cne $Expected) {
        throw "ASSERTION FAILED: $Message (expected '$Expected', got '$Actual')"
    }
}

function Assert-ThrowsWithoutSecret {
    param(
        [Parameter(Mandatory)]
        [scriptblock] $Action,
        [Parameter(Mandatory)]
        [string] $Secret,
        [Parameter(Mandatory)]
        [string] $Message
    )

    $Caught = $null
    try {
        & $Action
    }
    catch {
        $Caught = $_
    }
    Assert-True ($null -ne $Caught) $Message
    Assert-True (
        -not $Caught.ToString().Contains(
            $Secret,
            [StringComparison]::Ordinal
        )
    ) "$Message must not disclose the credential"
}

function Get-RequestLines {
    if (-not (Test-Path -LiteralPath $LogPath -PathType Leaf)) {
        return @()
    }
    return @(
        Get-Content -LiteralPath $LogPath |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
}

function Start-AsyncPowerShell {
    param(
        [Parameter(Mandatory)]
        [scriptblock] $Body,
        [Parameter(Mandatory)]
        [object[]] $Arguments
    )

    $Pipeline = [powershell]::Create()
    [void] $Pipeline.AddScript($Body)
    foreach ($Argument in $Arguments) {
        [void] $Pipeline.AddArgument($Argument)
    }
    $Handle = $Pipeline.BeginInvoke()
    return [pscustomobject]@{
        Pipeline = $Pipeline
        Handle = $Handle
    }
}

function Receive-AsyncPowerShell {
    param(
        [Parameter(Mandatory)]
        [object] $Invocation,
        [int] $TimeoutSeconds = 20
    )

    if (-not $Invocation.Handle.AsyncWaitHandle.WaitOne(
        [TimeSpan]::FromSeconds($TimeoutSeconds)
    )) {
        $Invocation.Pipeline.Stop()
        throw [TimeoutException]::new(
            'A protected concurrent invocation did not complete in time.'
        )
    }
    try {
        $Items = @($Invocation.Pipeline.EndInvoke($Invocation.Handle))
        if ($Invocation.Pipeline.HadErrors) {
            $Details = (
                $Invocation.Pipeline.Streams.Error |
                    ForEach-Object { $_.ToString() }
            ) -join '; '
            throw "A protected concurrent invocation failed: $Details"
        }
        if ($Items.Count -eq 1) {
            return $Items[0]
        }
        return $Items
    }
    finally {
        $Invocation.Pipeline.Dispose()
    }
}

try {
    $ExpectedOperationId = 'Vcenter.Authorization.Roles_list'
    $ExpectedSpecPath = (
        'specifications/vsphere/openapi/automation/vcenter.yaml'
    )
    $ExpectedCommit = (
        '3949fc33339fc5ea1b77eadb258f1cf49aa88e26'
    )

    $Contract = Get-Content -Raw -LiteralPath $ContractPath |
        ConvertFrom-Json
    $Sources = Get-Content -Raw -LiteralPath $SourcesPath |
        ConvertFrom-Json

    Assert-Equal $Sources.repository 'vmware/vcf-api-specs' `
        'official repository'
    Assert-Equal $Sources.repositoryCommitSha $ExpectedCommit `
        'pinned VCF 9.1 repository commit'
    Assert-Equal $Sources.specPath $ExpectedSpecPath `
        'official specification path'
    Assert-Equal $Sources.specBlobSha `
        '8028b0824c4ff3503d05f44814f967938a795c40' `
        'pinned specification blob'
    Assert-Equal (@($Sources.operationIds) -join ',') `
        $ExpectedOperationId 'official operationIds'
    Assert-Equal @($Sources.operations).Count 1 `
        'official operation record count'
    Assert-Equal $Sources.operations[0].operationId `
        $ExpectedOperationId 'official operation record'
    Assert-Equal $Sources.operations[0].repositoryCommitSha `
        $ExpectedCommit 'operation records repository commit'
    Assert-Equal $Sources.operations[0].specPath `
        $ExpectedSpecPath 'operation records specification path'

    Assert-Equal $Contract.source.openapi '3.0.3' `
        'contract OpenAPI version'
    Assert-Equal $Contract.source.apiVersion '9.1.0.0' `
        'contract API version'
    Assert-Equal $Contract.source.specPath $ExpectedSpecPath `
        'contract specification path'
    Assert-Equal $Contract.source.commitSha $ExpectedCommit `
        'contract repository commit'
    Assert-Equal $Contract.source.specBlobSha $Sources.specBlobSha `
        'contract and provenance blob agree'
    Assert-Equal $Contract.securitySchemes.api_key_auth.name `
        'vmware-api-session-id' 'contract authentication header'
    Assert-Equal @($Contract.operations).Count 1 `
        'focused contract operation count'
    $Operation = $Contract.operations[0]
    Assert-Equal $Operation.operationId $ExpectedOperationId `
        'contract operationId'
    Assert-Equal $Operation.method 'GET' 'contract method'
    Assert-Equal $Operation.specPathItem `
        '/vcenter/authorization/roles' 'specification path item'
    Assert-Equal $Operation.path `
        '/api/vcenter/authorization/roles' 'wire path'
    Assert-Equal $Operation.requestBody $false `
        'role listing has no request body'
    Assert-Equal (($Operation.effectiveQueryFields.name) -join ',') `
        'is_system,names,privileges,page_size,marker' `
        'contract optional query projection'
    foreach ($Field in $Operation.effectiveQueryFields) {
        Assert-Equal $Field.required $false `
            "$($Field.name) is optional"
        Assert-Equal $Field.unsetBehavior 'omit' `
            "$($Field.name) unset behavior"
    }
    Assert-Equal (
        $Contract.schemas.'Vcenter.Authorization.Roles.IterationSpec'.`
            properties.page_size.defaultWhenMissing
    ) 200 'specification page-size default'
    Assert-Equal (
        $Contract.schemas.'Vcenter.Authorization.Roles.ListResult'.required `
            -join ','
    ) 'items' 'list result required fields'

    $Manifest = Import-PowerShellDataFile -LiteralPath $ManifestPath
    Assert-Equal @($Manifest.RequiredModules).Count 1 `
        'manifest prerequisite count'
    Assert-Equal $Manifest.RequiredModules[0].ModuleName `
        'VMware.Sdk.Vcf.SddcManager' 'VCF PowerCLI module prerequisite'
    Assert-Equal ([version] $Manifest.RequiredModules[0].ModuleVersion) `
        ([version] '13.5.0.25380678') 'VCF PowerCLI module version'
    Assert-Equal (($Manifest.FunctionsToExport) -join ',') (
        'New-VcfVcenterCredentialClient,' +
        'Get-VcfVcenterAuthorizationRole,' +
        'Set-VcfVcenterCredential'
    ) 'manifest exports'
    Assert-Equal @(
        Get-ChildItem -LiteralPath $Root -Recurse -File |
            Where-Object {
                $_.Extension -in @(
                    '.dll',
                    '.nupkg',
                    '.snupkg',
                    '.yaml',
                    '.yml'
                )
            }
    ).Count 0 'seed does not vendor modules, assemblies, or OpenAPI files'

    $Tokens = $null
    $ParseErrors = $null
    $null = [Management.Automation.Language.Parser]::ParseFile(
        $ModulePath,
        [ref] $Tokens,
        [ref] $ParseErrors
    )
    Assert-Equal @($ParseErrors).Count 0 'module parses without errors'
    $SourceText = Get-Content -Raw -LiteralPath $ModulePath
    foreach ($RequiredText in @(
        'VMware.Sdk.OpenApi.Cmdlets.IServerConnection',
        '.GetClient()',
        'vmware-api-session-id',
        'System.Threading.Monitor',
        'Net.Http.HttpRequestMessage'
    )) {
        Assert-True $SourceText.Contains($RequiredText) `
            "implementation must use $RequiredText"
    }
    foreach ($ForbiddenText in @(
        'Invoke-RestMethod',
        'Invoke-WebRequest',
        'Start-Process',
        'curl',
        'Connect-VIServer'
    )) {
        Assert-True (-not $SourceText.Contains($ForbiddenText)) `
            "implementation must not use $ForbiddenText"
    }
    Assert-True (
        $SourceText -notmatch '(?m)^\s*SessionToken\s*='
    ) 'client state must not publish a SessionToken property'

    Import-Module $ManifestPath -Force -ErrorAction Stop
    $Exports = @(
        Get-Command -Module VcfVcenterCredentialGate `
            -CommandType Function |
            Sort-Object Name |
            ForEach-Object Name
    )
    Assert-Equal ($Exports -join ',') (
        'Get-VcfVcenterAuthorizationRole,' +
        'New-VcfVcenterCredentialClient,' +
        'Set-VcfVcenterCredential'
    ) 'runtime exports'
    $NewCommand = Get-Command New-VcfVcenterCredentialClient
    Assert-Equal $NewCommand.Parameters.Connection.ParameterType.FullName `
        'VMware.Sdk.OpenApi.Cmdlets.IServerConnection' `
        'constructor connection type'
    $SetCommand = Get-Command Set-VcfVcenterCredential
    Assert-Equal $SetCommand.Parameters.Connection.ParameterType.FullName `
        'VMware.Sdk.OpenApi.Cmdlets.IServerConnection' `
        'replacement connection type'

    $RunId = [guid]::NewGuid().ToString('N')
    $OldToken = 'old-session-' + $RunId
    $NewToken = 'new-session-' + $RunId
    $OldItem = [ordered]@{
        role = 'old-role-' + $RunId.Substring(0, 8)
        info = [ordered]@{
            name = 'Old request ' + $RunId.Substring(8, 6)
            description = 'admitted before cutover'
            privileges = @('System.Read')
            system = $false
        }
    }
    $NewItem = [ordered]@{
        role = 'new-role-' + $RunId.Substring(14, 8)
        info = [ordered]@{
            name = 'New request ' + $RunId.Substring(22, 6)
            description = 'admitted after cutover'
            privileges = @('System.Read', 'System.View')
            system = $true
        }
    }
    $Scenario = [ordered]@{
        old_token = $OldToken
        new_token = $NewToken
        old_item = $OldItem
        new_item = $NewItem
        release_file = $ReleasePath
    }
    [IO.File]::WriteAllText(
        $ScenarioPath,
        ($Scenario | ConvertTo-Json -Depth 8 -Compress),
        [Text.UTF8Encoding]::new($false)
    )

    $MockProcess = Start-Process -FilePath 'python3' -ArgumentList @(
        '-B',
        $MockPath,
        $PortPath,
        $LogPath,
        $ContractPath,
        $ScenarioPath
    ) -PassThru -RedirectStandardOutput $StdoutPath `
      -RedirectStandardError $StderrPath

    $Deadline = [Diagnostics.Stopwatch]::StartNew()
    while (-not (Test-Path -LiteralPath $PortPath -PathType Leaf)) {
        if ($MockProcess.HasExited) {
            $Details = Get-Content -Raw -LiteralPath $StderrPath `
                -ErrorAction SilentlyContinue
            throw "loopback mock exited before startup: $Details"
        }
        if ($Deadline.Elapsed.TotalSeconds -gt 10) {
            throw 'timed out waiting for loopback mock startup'
        }
        Start-Sleep -Milliseconds 25
    }
    $Port = [int] (Get-Content -Raw -LiteralPath $PortPath)
    $BaseUrl = "http://127.0.0.1:$Port"

    $LeakedOriginSecret = 'origin-secret-' + $RunId
    Assert-ThrowsWithoutSecret -Secret $LeakedOriginSecret -Message (
        'constructor rejects embedded origin credentials'
    ) -Action {
        New-VcfVcenterCredentialClient `
            -Server "http://user:$LeakedOriginSecret@127.0.0.1:$Port/" `
            -SessionToken $OldToken
    }
    Assert-ThrowsWithoutSecret -Secret $OldToken -Message (
        'constructor rejects a non-root server path'
    ) -Action {
        New-VcfVcenterCredentialClient `
            -Server "$BaseUrl/api" `
            -SessionToken $OldToken
    }

    $Client = New-VcfVcenterCredentialClient `
        -Server $BaseUrl `
        -SessionToken $OldToken
    Assert-Equal (
        ($Client.PSObject.Properties.Name) -join ','
    ) 'SyncRoot,Generation,ActiveRequests,RotationPending,Version' `
        'client state contract'
    Assert-Equal (
        ($Client.Generation.PSObject.Properties.Name) -join ','
    ) 'BaseUri,HttpClient,OwnsClient' `
        'credential generation does not publish the session secret'
    Assert-Equal ([int] $Client.Version) 1 `
        'client starts at credential version 1'
    Assert-Equal ([int] $Client.ActiveRequests) 0 `
        'client starts without leases'
    Assert-Equal ([bool] $Client.RotationPending) $false `
        'client starts with admission open'
    Assert-True (-not (Test-Path -LiteralPath $LogPath)) `
        'client creation performs no API request'

    Assert-ThrowsWithoutSecret -Secret $OldToken -Message (
        'invalid replacement is rejected locally'
    ) -Action {
        Set-VcfVcenterCredential `
            -Client $Client `
            -SessionToken ' '
    }
    Assert-Equal ([int] $Client.Version) 1 `
        'invalid replacement leaves version unchanged'
    Assert-Equal ([bool] $Client.RotationPending) $false `
        'invalid replacement leaves admission open'
    Assert-True (-not (Test-Path -LiteralPath $LogPath)) `
        'invalid replacement performs no API request'

    $OldInvocation = Start-AsyncPowerShell -Body {
        param($Path, $SharedClient)
        $ErrorActionPreference = 'Stop'
        Import-Module $Path -Force -ErrorAction Stop > $null
        Get-VcfVcenterAuthorizationRole -Client $SharedClient
    } -Arguments @($ManifestPath, $Client)

    $Deadline = [Diagnostics.Stopwatch]::StartNew()
    while (@(Get-RequestLines).Count -lt 1) {
        if ($OldInvocation.Handle.AsyncWaitHandle.WaitOne(0)) {
            [void] (Receive-AsyncPowerShell -Invocation $OldInvocation)
            throw 'the old request completed before the mock released it'
        }
        if ($Deadline.Elapsed.TotalSeconds -gt 10) {
            throw 'timed out waiting for the old request to reach the mock'
        }
        Start-Sleep -Milliseconds 20
    }
    Assert-Equal ([int] $Client.ActiveRequests) 1 `
        'old request retains its credential lease while response is pending'

    $RotationInvocation = Start-AsyncPowerShell -Body {
        param($Path, $SharedClient, $ReplacementToken)
        $ErrorActionPreference = 'Stop'
        Import-Module $Path -Force -ErrorAction Stop > $null
        Set-VcfVcenterCredential `
            -Client $SharedClient `
            -SessionToken $ReplacementToken
    } -Arguments @($ManifestPath, $Client, $NewToken)

    $Deadline = [Diagnostics.Stopwatch]::StartNew()
    $RotationClaimed = $false
    while (-not $RotationClaimed) {
        [System.Threading.Monitor]::Enter($Client.SyncRoot)
        try {
            $RotationClaimed = [bool] $Client.RotationPending
        }
        finally {
            [System.Threading.Monitor]::Exit($Client.SyncRoot)
        }
        if ($RotationInvocation.Handle.AsyncWaitHandle.WaitOne(0)) {
            [void] (
                Receive-AsyncPowerShell -Invocation $RotationInvocation
            )
            throw 'credential cutover completed before the old request drained'
        }
        if ($Deadline.Elapsed.TotalSeconds -gt 10) {
            throw 'timed out waiting for credential cutover to close admission'
        }
        Start-Sleep -Milliseconds 20
    }

    $QueuedAttempting = [System.Threading.ManualResetEventSlim]::new($false)
    $QueuedInvocation = Start-AsyncPowerShell -Body {
        param($Path, $SharedClient, $Attempting)
        $ErrorActionPreference = 'Stop'
        Import-Module $Path -Force -ErrorAction Stop > $null
        [void] $Attempting.Set()
        Get-VcfVcenterAuthorizationRole -Client $SharedClient
    } -Arguments @($ManifestPath, $Client, $QueuedAttempting)

    Assert-True (
        $QueuedAttempting.Wait([TimeSpan]::FromSeconds(10))
    ) 'queued request did not attempt admission'
    Start-Sleep -Milliseconds 300
    Assert-True (
        -not $RotationInvocation.Handle.AsyncWaitHandle.WaitOne(0)
    ) 'credential cutover waits for the old request to drain'
    Assert-True (
        -not $QueuedInvocation.Handle.AsyncWaitHandle.WaitOne(0)
    ) 'request arriving during cutover waits for the new credential'
    Assert-Equal @(Get-RequestLines).Count 1 `
        'no second request reaches the wire while cutover is pending'
    Assert-Equal ([int] $Client.Version) 1 `
        'replacement is not published before the old request drains'
    Assert-Equal ([int] $Client.ActiveRequests) 1 `
        'queued request is not admitted onto the old generation'

    [IO.File]::WriteAllText(
        $ReleasePath,
        $RunId,
        [Text.UTF8Encoding]::new($false)
    )
    $OldResult = Receive-AsyncPowerShell -Invocation $OldInvocation
    $OldInvocation = $null
    $RotationResult = Receive-AsyncPowerShell `
        -Invocation $RotationInvocation
    $RotationInvocation = $null
    $QueuedResult = Receive-AsyncPowerShell `
        -Invocation $QueuedInvocation
    $QueuedInvocation = $null

    Assert-Equal $OldResult.role $OldItem.role `
        'old request returns the old response'
    Assert-Equal $OldResult.info.name $OldItem.info.name `
        'old response is fully decoded before lease release'
    Assert-Equal $QueuedResult.role $NewItem.role `
        'queued request returns the post-cutover response'
    Assert-Equal $QueuedResult.info.name $NewItem.info.name `
        'queued response is decoded'
    Assert-Equal (
        ($RotationResult.PSObject.Properties.Name) -join ','
    ) 'PreviousVersion,CurrentVersion' 'cutover result property contract'
    Assert-Equal ([int] $RotationResult.PreviousVersion) 1 `
        'cutover reports prior version'
    Assert-Equal ([int] $RotationResult.CurrentVersion) 2 `
        'cutover reports replacement version'
    Assert-Equal ([int] $Client.Version) 2 `
        'client publishes exactly one replacement generation'
    Assert-Equal ([int] $Client.ActiveRequests) 0 `
        'all request leases are released'
    Assert-Equal ([bool] $Client.RotationPending) $false `
        'admission reopens after cutover'

    $LogLines = @(Get-RequestLines)
    Assert-Equal $LogLines.Count 2 `
        'exactly one request per admitted call with no retry'
    $Requests = @($LogLines | ForEach-Object { $_ | ConvertFrom-Json })
    $ExpectedTokens = @($OldToken, $NewToken)
    for ($Index = 0; $Index -lt 2; $Index++) {
        $Request = $Requests[$Index]
        Assert-Equal $Request.operationId $ExpectedOperationId `
            "request $Index operationId"
        Assert-Equal ([int] $Request.sequenceIndex) $Index `
            "request $Index sequence index"
        Assert-Equal $Request.requestValid $true `
            "request $Index satisfies the pinned contract"
        Assert-Equal $Request.method 'GET' "request $Index method"
        Assert-Equal $Request.rawTarget `
            '/api/vcenter/authorization/roles' `
            "request $Index exact target"
        Assert-Equal $Request.path `
            '/api/vcenter/authorization/roles' `
            "request $Index path"
        Assert-Equal $Request.rawQuery '' `
            "request $Index omits the complete query string"
        Assert-Equal $Request.vmwareApiSessionId $ExpectedTokens[$Index] `
            "request $Index uses its credential generation"
        Assert-Equal $Request.authorization $null `
            "request $Index omits Authorization"
        Assert-Equal $Request.accept 'application/json' `
            "request $Index Accept header"
        Assert-Equal $Request.contentType $null `
            "request $Index omits Content-Type"
        Assert-Equal ([int] $Request.contentLength) 0 `
            "request $Index has no content bytes"
        Assert-Equal $Request.bodyHex '' `
            "request $Index body is byte-empty"
        Assert-Equal ([int] $Request.status) 200 `
            "request $Index receives the documented status"
        foreach ($Omitted in @(
            'filter',
            'iterate',
            'is_system',
            'names',
            'privileges',
            'page_size',
            'marker'
        )) {
            Assert-True (
                -not $Request.rawTarget.Contains(
                    $Omitted,
                    [StringComparison]::Ordinal
                )
            ) "request $Index omits optional field $Omitted"
        }
    }

    'PASS: drain-safe vCenter credential cutover and exact role-list wire shape'
}
finally {
    foreach ($Invocation in @(
        $OldInvocation,
        $RotationInvocation,
        $QueuedInvocation
    )) {
        if ($null -ne $Invocation) {
            try {
                $Invocation.Pipeline.Stop()
            }
            catch {
            }
            $Invocation.Pipeline.Dispose()
        }
    }
    if ($null -ne $QueuedAttempting) {
        $QueuedAttempting.Dispose()
    }
    if ($null -ne $MockProcess -and -not $MockProcess.HasExited) {
        Stop-Process -Id $MockProcess.Id -Force -ErrorAction SilentlyContinue
        $MockProcess.WaitForExit()
    }
    Remove-Module VcfVcenterCredentialGate `
        -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force `
            -ErrorAction SilentlyContinue
    }
}
