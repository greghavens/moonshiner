$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $Root (
    'VcfVcenterResizeReport/VcfVcenterResizeReport.psd1'
)
$ModulePath = Join-Path $Root (
    'VcfVcenterResizeReport/VcfVcenterResizeReport.psm1'
)
$ContractPath = Join-Path $Root 'docs/contract.json'
$SourcesPath = Join-Path $Root 'docs/official_sources.json'
$MockPath = Join-Path $PSScriptRoot 'mock_vcenter.py'
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'vcf91-0093-' + [guid]::NewGuid().ToString('N')
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
    $ExpectedOperationIds = @(
        'Vcenter.Vm.Hardware.Cpu_update',
        'Vcenter.Vm.Hardware.Memory_update',
        'Vcenter.Vm.Power_start'
    )
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
        ($ExpectedOperationIds -join ',') 'official operationIds'
    Assert-Equal @($Sources.operations).Count 3 `
        'official operation record count'
    for ($Index = 0; $Index -lt 3; $Index++) {
        Assert-Equal $Sources.operations[$Index].operationId `
            $ExpectedOperationIds[$Index] `
            "official operation $Index operationId"
        Assert-Equal $Sources.operations[$Index].repositoryCommitSha `
            $ExpectedCommit "official operation $Index records commit"
        Assert-Equal $Sources.operations[$Index].specPath `
            $ExpectedSpecPath "official operation $Index records spec path"
    }

    Assert-Equal $Contract.source.openapi '3.0.3' `
        'contract OpenAPI version'
    Assert-Equal $Contract.source.apiVersion '9.1.0.0' `
        'contract API version'
    Assert-Equal $Contract.source.specPath $ExpectedSpecPath `
        'contract specification path'
    Assert-Equal $Contract.source.commitSha $ExpectedCommit `
        'contract commit'
    Assert-Equal $Contract.source.specBlobSha $Sources.specBlobSha `
        'contract and provenance blob agree'
    Assert-Equal $Contract.securitySchemes.api_key_auth.name `
        'vmware-api-session-id' 'contract authentication header'
    Assert-Equal @($Contract.operations).Count 3 `
        'focused contract operation count'
    Assert-Equal (($Contract.operations.operationId) -join ',') `
        ($ExpectedOperationIds -join ',') 'contract operation order'
    Assert-Equal (($Contract.operations.method) -join ',') `
        'PATCH,PATCH,POST' 'contract methods'
    Assert-Equal $Contract.operations[0].path `
        '/api/vcenter/vm/{vm}/hardware/cpu' 'CPU update route'
    Assert-Equal $Contract.operations[1].path `
        '/api/vcenter/vm/{vm}/hardware/memory' 'memory update route'
    Assert-Equal $Contract.operations[2].path `
        '/api/vcenter/vm/{vm}/power?action=start' 'power start route'
    Assert-Equal $Contract.operations[2].requestBody $false `
        'power start has no request body'
    foreach ($Operation in $Contract.operations) {
        Assert-True (
            $Operation.responses.PSObject.Properties.Name -contains '204'
        ) "$($Operation.operationId) records its 204 response"
        Assert-Equal $Operation.responses.'503'.schema `
            'Vapi.Std.Errors.ServiceUnavailable' `
            "$($Operation.operationId) records its 503 schema"
    }
    $CpuProperties = @(
        $Contract.schemas.'Vcenter.Vm.Hardware.Cpu.UpdateSpec'.`
            properties.PSObject.Properties.Name
    )
    Assert-Equal ($CpuProperties -join ',') `
        'count,cores_per_socket,hot_add_enabled,hot_remove_enabled' `
        'CPU update property projection'
    Assert-Equal @(
        $Contract.schemas.'Vcenter.Vm.Hardware.Cpu.UpdateSpec'.required
    ).Count 0 'CPU update has no required properties'
    $MemoryProperties = @(
        $Contract.schemas.'Vcenter.Vm.Hardware.Memory.UpdateSpec'.`
            properties.PSObject.Properties.Name
    )
    Assert-Equal ($MemoryProperties -join ',') `
        'size_mib,hot_add_enabled' 'memory update property projection'
    Assert-Equal @(
        $Contract.schemas.'Vcenter.Vm.Hardware.Memory.UpdateSpec'.required
    ).Count 0 'memory update has no required properties'
    Assert-Equal (
        $Contract.schemas.'Vapi.Std.Errors.Error'.required -join ','
    ) 'error_type,messages' 'standard error required fields'

    $Manifest = Import-PowerShellDataFile -LiteralPath $ManifestPath
    Assert-Equal @($Manifest.RequiredModules).Count 1 `
        'manifest prerequisite count'
    Assert-Equal $Manifest.RequiredModules[0].ModuleName `
        'VMware.Sdk.Vcf.SddcManager' 'VCF PowerCLI module prerequisite'
    Assert-Equal ([version] $Manifest.RequiredModules[0].ModuleVersion) `
        ([version] '13.5.0.25380678') 'VCF PowerCLI module version'
    Assert-Equal (($Manifest.FunctionsToExport) -join ',') `
        'New-VcfVcenterResizeClient,Set-VcfVmResizeAndStart' `
        'manifest exports'
    Assert-Equal @(
        Get-ChildItem -LiteralPath $Root -Recurse -File |
            Where-Object {
                $_.Extension -in @(
                    '.dll',
                    '.nupkg',
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
        'EscapeDataString',
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

    Import-Module $ManifestPath -Force -ErrorAction Stop
    $Exports = @(
        Get-Command -Module VcfVcenterResizeReport -CommandType Function |
            Sort-Object Name |
            ForEach-Object Name
    )
    Assert-Equal ($Exports -join ',') `
        'New-VcfVcenterResizeClient,Set-VcfVmResizeAndStart' `
        'runtime exports'
    $NewCommand = Get-Command New-VcfVcenterResizeClient
    Assert-Equal $NewCommand.Parameters.Connection.ParameterType.FullName `
        'VMware.Sdk.OpenApi.Cmdlets.IServerConnection' `
        'authenticated VCF PowerCLI connection type'
    $SetCommand = Get-Command Set-VcfVmResizeAndStart
    Assert-Equal $SetCommand.Parameters.CpuCount.ParameterType.FullName `
        'System.Int64' 'CPU count parameter type'
    Assert-Equal $SetCommand.Parameters.MemoryMiB.ParameterType.FullName `
        'System.Int64' 'memory parameter type'

    $RunId = [guid]::NewGuid().ToString('N')
    $SessionToken = 'session-' + $RunId
    $Vm = 'vm ' + $RunId.Substring(0, 10) + '/blue+snow-' + [char] 0x96EA
    $CpuCount = [long] (
        4 + ([Convert]::ToInt32($RunId.Substring(10, 2), 16) % 12)
    )
    $MemoryMiB = [long] (
        8192 +
        (
            [Convert]::ToInt32($RunId.Substring(12, 2), 16) % 64
        ) * 128
    )
    $PowerErrorMessage = (
        'runtime power capacity unavailable ' + $RunId.Substring(14, 12)
    )
    $Scenario = [ordered]@{
        session_token = $SessionToken
        vm = $Vm
        cpu_count = $CpuCount
        memory_mib = $MemoryMiB
        power_error_message = $PowerErrorMessage
    }
    [IO.File]::WriteAllText(
        $ScenarioPath,
        ($Scenario | ConvertTo-Json -Depth 5 -Compress),
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

    $Client = New-VcfVcenterResizeClient `
        -Server $BaseUrl `
        -SessionToken $SessionToken
    Assert-True (-not (Test-Path -LiteralPath $LogPath)) `
        'client creation performs no API request'

    $Report = Set-VcfVmResizeAndStart `
        -Client $Client `
        -Vm $Vm `
        -CpuCount $CpuCount `
        -MemoryMiB $MemoryMiB
    Assert-True ($null -ne $Report) 'workflow returns a report'
    Assert-Equal $Report.Vm $Vm 'report VM'
    Assert-Equal $Report.OverallState 'FAILED' `
        'report overall failure'
    Assert-Equal ([int] $Report.CompletedStepCount) 2 `
        'report retains two completed steps'
    Assert-Equal $Report.FailedOperationId `
        'Vcenter.Vm.Power_start' 'report failed operation'
    $Steps = @($Report.Steps)
    Assert-Equal $Steps.Count 3 'report includes every attempted step'
    $ExpectedNames = @('Cpu', 'Memory', 'PowerStart')
    $ExpectedStates = @('SUCCEEDED', 'SUCCEEDED', 'FAILED')
    $ExpectedStatuses = @(204, 204, 503)
    for ($Index = 0; $Index -lt 3; $Index++) {
        Assert-Equal $Steps[$Index].Name $ExpectedNames[$Index] `
            "report step $Index name"
        Assert-Equal $Steps[$Index].OperationId `
            $ExpectedOperationIds[$Index] "report step $Index operationId"
        Assert-Equal $Steps[$Index].State $ExpectedStates[$Index] `
            "report step $Index state"
        Assert-Equal ([int] $Steps[$Index].HttpStatus) `
            $ExpectedStatuses[$Index] "report step $Index HTTP status"
    }
    foreach ($Index in @(0, 1)) {
        Assert-Equal $Steps[$Index].ErrorType $null `
            "successful step $Index has no error type"
        Assert-Equal $Steps[$Index].Message $null `
            "successful step $Index has no error message"
    }
    Assert-Equal $Steps[2].ErrorType 'SERVICE_UNAVAILABLE' `
        'failed step preserves vAPI error_type'
    Assert-Equal $Steps[2].Message $PowerErrorMessage `
        'failed step preserves first default_message'

    Start-Sleep -Milliseconds 150
    $LogLines = @(
        Get-Content -LiteralPath $LogPath |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    Assert-Equal $LogLines.Count 3 `
        'one request per operation with no retry or rollback'
    $Requests = @($LogLines | ForEach-Object { $_ | ConvertFrom-Json })
    $EncodedVm = [uri]::EscapeDataString($Vm)
    $ExpectedTargets = @(
        "/api/vcenter/vm/$EncodedVm/hardware/cpu",
        "/api/vcenter/vm/$EncodedVm/hardware/memory",
        "/api/vcenter/vm/$EncodedVm/power?action=start"
    )
    $CpuBody = (
        '{"count":' +
        $CpuCount.ToString([Globalization.CultureInfo]::InvariantCulture) +
        '}'
    )
    $MemoryBody = (
        '{"size_mib":' +
        $MemoryMiB.ToString([Globalization.CultureInfo]::InvariantCulture) +
        '}'
    )
    $ExpectedBodies = @($CpuBody, $MemoryBody, '')
    $ExpectedContentTypes = @(
        'application/json',
        'application/json',
        $null
    )
    for ($Index = 0; $Index -lt 3; $Index++) {
        $Request = $Requests[$Index]
        $ExpectedBytes = [Text.Encoding]::UTF8.GetBytes(
            $ExpectedBodies[$Index]
        )
        $ExpectedHex = [Convert]::ToHexString(
            $ExpectedBytes
        ).ToLowerInvariant()
        Assert-Equal $Request.operationId $ExpectedOperationIds[$Index] `
            "request $Index operationId"
        Assert-Equal ([int] $Request.sequenceIndex) $Index `
            "request $Index sequence index"
        Assert-Equal $Request.sequenceValid $true `
            "request $Index sequence is valid"
        Assert-Equal $Request.requestValid $true `
            "request $Index exact request shape is valid"
        Assert-Equal $Request.method @('PATCH', 'PATCH', 'POST')[$Index] `
            "request $Index method"
        Assert-Equal $Request.rawTarget $ExpectedTargets[$Index] `
            "request $Index exact target"
        Assert-Equal $Request.vmwareApiSessionId $SessionToken `
            "request $Index session header"
        Assert-Equal $Request.authorization $null `
            "request $Index omits Authorization"
        Assert-Equal $Request.accept 'application/json' `
            "request $Index accept header"
        Assert-Equal $Request.contentType $ExpectedContentTypes[$Index] `
            "request $Index content type"
        Assert-Equal ([int] $Request.contentLength) `
            $ExpectedBytes.Length "request $Index body byte count"
        Assert-Equal $Request.bodyHex $ExpectedHex `
            "request $Index exact body bytes"
        Assert-Equal ([int] $Request.status) $ExpectedStatuses[$Index] `
            "request $Index fixture status"
    }
    Assert-Equal $Requests[0].rawQuery '' 'CPU query is absent'
    Assert-Equal $Requests[1].rawQuery '' 'memory query is absent'
    Assert-Equal $Requests[2].rawQuery 'action=start' `
        'power action has only its fixed query'
    Assert-Equal $Requests[2].contentLength 0 `
        'power action has no request body'
    Assert-Equal $Requests[2].contentType $null `
        'power action has no content type'
    foreach ($Omitted in @(
        'cores_per_socket',
        'hot_add_enabled',
        'hot_remove_enabled'
    )) {
        Assert-True (-not $CpuBody.Contains($Omitted)) `
            "CPU request omits optional field $Omitted"
    }
    Assert-True (-not $MemoryBody.Contains('hot_add_enabled')) `
        'memory request omits optional hot_add_enabled'

    'PASS: exact vCenter resize wire shape and accurate partial-failure report'
}
finally {
    if ($null -ne $MockProcess -and -not $MockProcess.HasExited) {
        Stop-Process -Id $MockProcess.Id -Force -ErrorAction SilentlyContinue
        $MockProcess.WaitForExit()
    }
    Remove-Module VcfVcenterResizeReport -Force `
        -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force `
            -ErrorAction SilentlyContinue
    }
}
