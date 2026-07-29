$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $Root (
    'VcfVcenterCpuReconciler/VcfVcenterCpuReconciler.psd1'
)
$ModulePath = Join-Path $Root (
    'VcfVcenterCpuReconciler/VcfVcenterCpuReconciler.psm1'
)
$ContractPath = Join-Path $Root 'docs/contract.json'
$SourcesPath = Join-Path $Root 'docs/official_sources.json'
$MockPath = Join-Path $PSScriptRoot 'mock_vcenter.py'
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'vcf91-0092-' + [guid]::NewGuid().ToString('N')
)
[IO.Directory]::CreateDirectory($TempRoot) | Out-Null
$PortPath = Join-Path $TempRoot 'port.txt'
$LogPath = Join-Path $TempRoot 'requests.jsonl'
$ScenarioPath = Join-Path $TempRoot 'scenario.json'
$StdoutPath = Join-Path $TempRoot 'mock.stdout'
$StderrPath = Join-Path $TempRoot 'mock.stderr'
$MockProcess = $null

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

try {
    $Contract = Get-Content -Raw -LiteralPath $ContractPath |
        ConvertFrom-Json
    $Sources = Get-Content -Raw -LiteralPath $SourcesPath |
        ConvertFrom-Json

    Assert-Equal $Sources.repository 'vmware/vcf-api-specs' `
        'official repository'
    Assert-Equal $Sources.repositoryCommitSha `
        '3949fc33339fc5ea1b77eadb258f1cf49aa88e26' `
        'pinned VCF 9.1 repository commit'
    Assert-Equal $Sources.specPath `
        'specifications/vsphere/openapi/automation/vcenter.yaml' `
        'official specification path'
    Assert-Equal $Sources.specBlobSha `
        '8028b0824c4ff3503d05f44814f967938a795c40' `
        'pinned specification blob'
    Assert-Equal (@($Sources.operationIds) -join ',') `
        'Vcenter.Vm.Hardware.Cpu_get,Vcenter.Vm.Hardware.Cpu_update' `
        'official operationIds'
    Assert-Equal @($Sources.operations).Count 2 `
        'official operation record count'
    foreach ($SourceOperation in $Sources.operations) {
        Assert-Equal $SourceOperation.repositoryCommitSha `
            $Sources.repositoryCommitSha `
            "$($SourceOperation.operationId) records repository commit"
        Assert-Equal $SourceOperation.specPath $Sources.specPath `
            "$($SourceOperation.operationId) records specification path"
    }

    Assert-Equal $Contract.source.apiVersion '9.1.0.0' `
        'contract API version'
    Assert-Equal $Contract.source.specPath $Sources.specPath `
        'contract and provenance paths agree'
    Assert-Equal $Contract.source.commitSha `
        $Sources.repositoryCommitSha 'contract and provenance commits agree'
    Assert-Equal @($Contract.operations).Count 2 `
        'focused contract operation count'
    Assert-Equal (($Contract.operations.operationId) -join ',') `
        'Vcenter.Vm.Hardware.Cpu_get,Vcenter.Vm.Hardware.Cpu_update' `
        'contract operationIds'
    Assert-Equal (($Contract.operations.method) -join ',') `
        'GET,PATCH' 'contract methods'
    Assert-Equal (($Contract.operations.path | Select-Object -Unique) -join ',') `
        '/api/vcenter/vm/{vm}/hardware/cpu' `
        'contract API route'
    Assert-Equal $Contract.operations[1].requestBody.contentType `
        'application/json' 'update request media type'
    Assert-Equal $Contract.operations[1].requestBody.schema `
        'Vcenter.Vm.Hardware.Cpu.UpdateSpec' 'update request schema'
    Assert-Equal (
        $Contract.schemas.'Vcenter.Vm.Hardware.Cpu.Info'.required -join ','
    ) 'cores_per_socket,count,hot_add_enabled,hot_remove_enabled' `
        'CPU info required fields'
    $UpdateProperties = $Contract.schemas.`
        'Vcenter.Vm.Hardware.Cpu.UpdateSpec'.properties
    Assert-Equal (($UpdateProperties.PSObject.Properties.Name) -join ',') `
        'count,cores_per_socket,hot_add_enabled,hot_remove_enabled' `
        'update specification fields'
    Assert-Equal @(
        $UpdateProperties.PSObject.Properties |
            Where-Object { $_.Value.required -eq $true }
    ).Count 0 'all update fields are optional in the specification'

    $Manifest = Import-PowerShellDataFile -LiteralPath $ManifestPath
    Assert-Equal @($Manifest.RequiredModules).Count 1 `
        'manifest prerequisite count'
    Assert-Equal $Manifest.RequiredModules[0].ModuleName `
        'VMware.Sdk.Vcf.SddcManager' 'VCF PowerCLI module prerequisite'
    Assert-Equal ([version] $Manifest.RequiredModules[0].ModuleVersion) `
        ([version] '13.5.0.25380678') 'VCF PowerCLI module version'
    Assert-Equal (($Manifest.FunctionsToExport) -join ',') `
        'New-VcfVcenterCpuClient,Set-VcfVmCpuCount' `
        'manifest exports'
    Assert-Equal @(
        Get-ChildItem -LiteralPath $Root -Recurse -File |
            Where-Object {
                $_.Extension -in @('.dll', '.nupkg', '.yaml', '.yml')
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
        'HttpRequestMessage',
        'EscapeDataString'
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

    Import-Module $ManifestPath -Force -ErrorAction Stop
    $Exports = @(
        Get-Command -Module VcfVcenterCpuReconciler -CommandType Function |
            Sort-Object Name |
            ForEach-Object Name
    )
    Assert-Equal ($Exports -join ',') `
        'New-VcfVcenterCpuClient,Set-VcfVmCpuCount' `
        'runtime exports'
    $NewCommand = Get-Command New-VcfVcenterCpuClient
    Assert-Equal $NewCommand.Parameters.Connection.ParameterType.FullName `
        'VMware.Sdk.OpenApi.Cmdlets.IServerConnection' `
        'authenticated VCF PowerCLI connection type'
    $SetCommand = Get-Command Set-VcfVmCpuCount
    Assert-Equal $SetCommand.Parameters.Vm.ParameterType.FullName `
        'System.String' 'VM parameter type'
    Assert-Equal $SetCommand.Parameters.Count.ParameterType.FullName `
        'System.Int64' 'CPU-count parameter type'

    $RunId = [guid]::NewGuid().ToString('N')
    $SessionToken = 'session-' + $RunId
    $Vm = 'vm retry+' + $RunId.Substring(0, 12)
    $InitialCount = [long] 2
    $DesiredCount = [long] 6
    $SuccessfulCount = [long] 8
    $Scenario = [ordered]@{
        session_token = $SessionToken
        vm = $Vm
        desired_count = $DesiredCount
        successful_count = $SuccessfulCount
        initial_info = [ordered]@{
            count = $InitialCount
            cores_per_socket = [long] 1
            hot_add_enabled = $true
            hot_remove_enabled = $false
        }
    }
    [IO.File]::WriteAllText(
        $ScenarioPath,
        ($Scenario | ConvertTo-Json -Depth 6 -Compress),
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

    $Client = New-VcfVcenterCpuClient `
        -Server $BaseUrl `
        -SessionToken $SessionToken
    Assert-True (-not (Test-Path -LiteralPath $LogPath)) `
        'client creation performs no API request'

    $FirstError = $null
    try {
        $null = Set-VcfVmCpuCount `
            -Client $Client `
            -Vm $Vm `
            -Count $DesiredCount
    }
    catch {
        $FirstError = $_.Exception
    }
    Assert-True ($null -ne $FirstError) `
        'the ambiguous HTTP 503 must be surfaced'
    Assert-True $FirstError.Message.Contains('503') `
        'the mutation failure identifies HTTP 503'

    $RetryResult = @(
        Set-VcfVmCpuCount `
            -Client $Client `
            -Vm $Vm `
            -Count $DesiredCount
    )
    Assert-Equal $RetryResult.Count 1 'retry returns one result'
    Assert-Equal $RetryResult[0].Vm $Vm 'retry result VM'
    Assert-Equal ([long] $RetryResult[0].PreviousCount) $DesiredCount `
        'retry observes the already-applied count'
    Assert-Equal ([long] $RetryResult[0].Count) $DesiredCount `
        'retry result desired count'
    Assert-Equal ([bool] $RetryResult[0].Changed) $false `
        'retry skips a duplicate mutation'

    $SuccessResult = @(
        Set-VcfVmCpuCount `
            -Client $Client `
            -Vm $Vm `
            -Count $SuccessfulCount
    )
    Assert-Equal $SuccessResult.Count 1 `
        'successful update returns one result'
    Assert-Equal $SuccessResult[0].Vm $Vm 'successful update result VM'
    Assert-Equal ([long] $SuccessResult[0].PreviousCount) $DesiredCount `
        'successful update reports the observed old count'
    Assert-Equal ([long] $SuccessResult[0].Count) $SuccessfulCount `
        'successful update reports the requested count'
    Assert-Equal ([bool] $SuccessResult[0].Changed) $true `
        'successful update reports a change'

    $StableResult = @(
        Set-VcfVmCpuCount `
            -Client $Client `
            -Vm $Vm `
            -Count $SuccessfulCount
    )
    Assert-Equal $StableResult.Count 1 `
        'stable retry returns one result'
    Assert-Equal ([long] $StableResult[0].PreviousCount) $SuccessfulCount `
        'stable retry observes the successful update'
    Assert-Equal ([long] $StableResult[0].Count) $SuccessfulCount `
        'stable retry result count'
    Assert-Equal ([bool] $StableResult[0].Changed) $false `
        'stable retry does not duplicate the successful update'

    $LogLines = @(
        Get-Content -LiteralPath $LogPath |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    Assert-Equal $LogLines.Count 6 `
        'wire sequence has one PATCH per distinct desired count'
    $Requests = @($LogLines | ForEach-Object { $_ | ConvertFrom-Json })
    $EncodedVm = [uri]::EscapeDataString($Vm)
    $ExpectedTarget = (
        '/api/vcenter/vm/' + $EncodedVm + '/hardware/cpu'
    )
    $ExpectedMethods = @('GET', 'PATCH', 'GET', 'GET', 'PATCH', 'GET')
    $ExpectedOperationIds = @(
        'Vcenter.Vm.Hardware.Cpu_get',
        'Vcenter.Vm.Hardware.Cpu_update',
        'Vcenter.Vm.Hardware.Cpu_get',
        'Vcenter.Vm.Hardware.Cpu_get',
        'Vcenter.Vm.Hardware.Cpu_update',
        'Vcenter.Vm.Hardware.Cpu_get'
    )
    $ExpectedStatuses = @(200, 503, 200, 200, 204, 200)

    for ($Index = 0; $Index -lt $Requests.Count; $Index++) {
        $Request = $Requests[$Index]
        Assert-Equal $Request.operationId $ExpectedOperationIds[$Index] `
            "request $Index operationId"
        Assert-Equal $Request.method $ExpectedMethods[$Index] `
            "request $Index method"
        Assert-Equal $Request.rawTarget $ExpectedTarget `
            "request $Index exact encoded target"
        Assert-Equal $Request.rawQuery '' `
            "request $Index omits the query string"
        Assert-Equal $Request.vmwareApiSessionId $SessionToken `
            "request $Index session header"
        Assert-Equal $Request.authorization $null `
            "request $Index omits Authorization"
        Assert-Equal $Request.accept 'application/json' `
            "request $Index accept header"
        Assert-Equal ([int] $Request.status) $ExpectedStatuses[$Index] `
            "request $Index response status"
    }

    foreach ($Index in @(0, 2, 3, 5)) {
        $Request = $Requests[$Index]
        Assert-Equal $Request.contentType $null `
            "GET request $Index omits content type"
        Assert-Equal ([int] $Request.contentLength) 0 `
            "GET request $Index sends an empty body"
        Assert-Equal $Request.bodyHex '' `
            "GET request $Index has zero body bytes"
        Assert-Equal $Request.bodyJson $null `
            "GET request $Index has no JSON body"
    }

    $PatchIndexes = @(1, 4)
    $PatchCounts = @($DesiredCount, $SuccessfulCount)
    for ($PatchNumber = 0; $PatchNumber -lt 2; $PatchNumber++) {
        $PatchRequest = $Requests[$PatchIndexes[$PatchNumber]]
        $ExpectedCount = $PatchCounts[$PatchNumber]
        Assert-Equal $PatchRequest.contentType 'application/json' `
            "PATCH $PatchNumber content type"
        $ExpectedJson = (
            '{"count":' +
            $ExpectedCount.ToString(
                [Globalization.CultureInfo]::InvariantCulture
            ) +
            '}'
        )
        $ExpectedHex = [Convert]::ToHexString(
            [Text.Encoding]::UTF8.GetBytes($ExpectedJson)
        ).ToLowerInvariant()
        Assert-Equal ([int] $PatchRequest.contentLength) `
            ([Text.Encoding]::UTF8.GetByteCount($ExpectedJson)) `
            "PATCH $PatchNumber content length"
        Assert-Equal $PatchRequest.bodyHex $ExpectedHex `
            "PATCH $PatchNumber exact UTF-8 JSON body bytes"
        Assert-Equal (
            ($PatchRequest.bodyJson.PSObject.Properties.Name) -join ','
        ) 'count' "PATCH $PatchNumber sends only count"
        Assert-Equal ([long] $PatchRequest.bodyJson.count) $ExpectedCount `
            "PATCH $PatchNumber count value"
        foreach ($UnsetField in @(
            'cores_per_socket',
            'hot_add_enabled',
            'hot_remove_enabled'
        )) {
            Assert-True (
                $PatchRequest.bodyJson.PSObject.Properties.Name -cnotcontains
                    $UnsetField
            ) "PATCH $PatchNumber omits unset field $UnsetField"
        }
    }

    Write-Output 'verification passed'
}
finally {
    if ($null -ne $MockProcess -and -not $MockProcess.HasExited) {
        Stop-Process -Id $MockProcess.Id -Force -ErrorAction SilentlyContinue
        $MockProcess.WaitForExit(5000)
    }
    Remove-Module VcfVcenterCpuReconciler -Force `
        -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force `
            -ErrorAction SilentlyContinue
    }
}
