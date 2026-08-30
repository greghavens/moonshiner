$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$TaskRoot = Split-Path -Parent $PSScriptRoot
$ModulePath = Join-Path $TaskRoot 'VcfInstallerChange.psm1'
$ContractPath = Join-Path $TaskRoot 'docs/contract.json'
$SourcesPath = Join-Path $TaskRoot 'docs/official_sources.json'
$MockPath = Join-Path $PSScriptRoot 'mock_vcf_installer.py'
$PinnedCommit = '3949fc33339fc5ea1b77eadb258f1cf49aa88e26'
$SpecPath = 'specifications/vcf-installer/vcf-installer-openapi.json'
$MinimumSdkVersion = [version]'13.5.0.25380678'
$SdkConnectionProbePath = '/v1/sddc-manager'

function Assert-True {
    param(
        [Parameter(Mandatory)]
        [bool]$Condition,
        [Parameter(Mandatory)]
        [string]$Message
    )
    if (-not $Condition) {
        throw "Verification failed: $Message"
    }
}

function Assert-Equal {
    param(
        [AllowNull()]
        [object]$Actual,
        [AllowNull()]
        [object]$Expected,
        [Parameter(Mandatory)]
        [string]$Message
    )
    if ($null -eq $Actual -and $null -eq $Expected) {
        return
    }
    if ($null -eq $Actual -or $null -eq $Expected -or "$Actual" -cne "$Expected") {
        throw "Verification failed: $Message. Expected '$Expected', got '$Actual'."
    }
}

function Assert-PropertyOrder {
    param(
        [Parameter(Mandatory)]
        [object]$Object,
        [Parameter(Mandatory)]
        [string[]]$Names,
        [Parameter(Mandatory)]
        [string]$Message
    )
    $actualNames = @($Object.PSObject.Properties.Name)
    Assert-Equal ($actualNames -join ',') ($Names -join ',') $Message
}

function Assert-JsonMemberSet {
    param(
        [Parameter(Mandatory)]
        [object]$Object,
        [Parameter(Mandatory)]
        [string[]]$Names,
        [Parameter(Mandatory)]
        [string]$Message
    )
    $actualNames = @($Object.PSObject.Properties.Name | Sort-Object)
    $expectedNames = @($Names | Sort-Object)
    Assert-Equal ($actualNames -join ',') ($expectedNames -join ',') $Message
}

function Assert-ReportShape {
    param(
        [Parameter(Mandatory)]
        [object]$Report,
        [Parameter(Mandatory)]
        [string]$Outcome,
        [Parameter(Mandatory)]
        [string]$Label
    )

    Assert-True ($Report -isnot [array]) "$Label must return one report object"
    Assert-PropertyOrder $Report @('Outcome', 'Steps') "$Label report property order"
    Assert-Equal $Report.Outcome $Outcome "$Label outcome"
    Assert-Equal @($Report.Steps).Count 3 "$Label step count"
    foreach ($step in @($Report.Steps)) {
        Assert-PropertyOrder $step @(
            'Name',
            'OperationId',
            'Status',
            'TaskId',
            'TaskStatus',
            'ErrorCode',
            'ErrorMessage'
        ) "$Label step property order for $($step.Name)"
    }
}

function Assert-Step {
    param(
        [Parameter(Mandatory)]
        [object]$Step,
        [Parameter(Mandatory)]
        [string]$Name,
        [Parameter(Mandatory)]
        [string]$OperationId,
        [Parameter(Mandatory)]
        [string]$Status,
        [AllowNull()]
        [object]$TaskId,
        [AllowNull()]
        [object]$TaskStatus,
        [AllowNull()]
        [object]$ErrorCode,
        [AllowNull()]
        [object]$ErrorMessage,
        [Parameter(Mandatory)]
        [string]$Label
    )

    Assert-Equal $Step.Name $Name "$Label name"
    Assert-Equal $Step.OperationId $OperationId "$Label operationId"
    Assert-Equal $Step.Status $Status "$Label status"
    Assert-Equal $Step.TaskId $TaskId "$Label task id"
    Assert-Equal $Step.TaskStatus $TaskStatus "$Label task status"
    Assert-Equal $Step.ErrorCode $ErrorCode "$Label error code"
    Assert-Equal $Step.ErrorMessage $ErrorMessage "$Label error message"
}

function Assert-SystemRequest {
    param(
        [Parameter(Mandatory)]
        [object]$Request,
        [Parameter(Mandatory)]
        [int]$Limit,
        [Parameter(Mandatory)]
        [string]$Label
    )

    Assert-JsonMemberSet $Request.json @('maxAllowedDomainsInSubscription') "$Label JSON member set"
    Assert-Equal $Request.json.maxAllowedDomainsInSubscription $Limit "$Label domain limit"
}

function Assert-ProxyRequest {
    param(
        [Parameter(Mandatory)]
        [object]$Request,
        [Parameter(Mandatory)]
        # Not $Host: that name belongs to the shell, which keeps it read-only,
        # and a parameter cannot be bound to it.
        [string]$ProxyHost,
        [Parameter(Mandatory)]
        [int]$Port,
        [Parameter(Mandatory)]
        [string]$Protocol,
        [Parameter(Mandatory)]
        [string]$Label
    )

    Assert-JsonMemberSet $Request.json @('isEnabled', 'host', 'port', 'transferProtocol') "$Label JSON member set"
    Assert-Equal $Request.json.isEnabled $true "$Label enabled value"
    Assert-Equal $Request.json.host $ProxyHost "$Label host"
    Assert-Equal $Request.json.port $Port "$Label port"
    Assert-Equal $Request.json.transferProtocol $Protocol "$Label transfer protocol"
}

foreach ($requiredFile in @($ModulePath, $ContractPath, $SourcesPath, $MockPath)) {
    Assert-True (Test-Path -LiteralPath $requiredFile -PathType Leaf) "Missing required file $requiredFile"
}

$vendoredFiles = @(Get-ChildItem -LiteralPath $TaskRoot -Recurse -File | Where-Object {
    $_.Extension -in @('.dll', '.nupkg', '.ni.dll') -or $_.Name -match '^VMware\..*\.ps[dm]1$'
})
Assert-Equal $vendoredFiles.Count 0 'VMware SDK binaries or modules must not be vendored'

$source = Get-Content -LiteralPath $ModulePath -Raw
$forbiddenPatterns = @(
    '(?i)\bInvoke-RestMethod\b',
    '(?i)\bInvoke-WebRequest\b',
    '(?i)\bSystem\.Net\.Http\b',
    '(?i)\bHttpClient\b',
    '(?i)\bWebClient\b',
    '(?i)\bTcpClient\b',
    '(?im)^\s*(curl|wget)\b'
)
foreach ($pattern in $forbiddenPatterns) {
    Assert-True (-not [regex]::IsMatch($source, $pattern)) "Raw transport is forbidden ($pattern)"
}

$requiredSdkCommands = @(
    'Connect-VcfInstallerServer',
    'Initialize-VcfInstallerSystemUpdateSpec',
    'Invoke-VcfInstallerUpdateSystemConfiguration',
    'Initialize-VcfInstallerProxyConfiguration',
    'Invoke-VcfInstallerUpdateProxyConfiguration',
    'Invoke-VcfInstallerGetTask',
    'Initialize-VcfInstallerCeipUpdateSpec',
    'Invoke-VcfInstallerSetCeipStatus'
)
foreach ($commandName in $requiredSdkCommands) {
    Assert-True ($source -cmatch "(?m)\b$([regex]::Escape($commandName))\b") "The implementation must use $commandName"
}

$contract = Get-Content -LiteralPath $ContractPath -Raw | ConvertFrom-Json -Depth 100
$sources = Get-Content -LiteralPath $SourcesPath -Raw | ConvertFrom-Json -Depth 100
$expectedOperations = @(
    [pscustomobject]@{ operationId = 'createToken'; method = 'POST'; path = '/v1/tokens' },
    [pscustomobject]@{ operationId = 'getApplianceInfo'; method = 'GET'; path = '/v1/system/appliance-info' },
    [pscustomobject]@{ operationId = 'updateSystemConfiguration'; method = 'PATCH'; path = '/v1/system' },
    [pscustomobject]@{ operationId = 'updateProxyConfiguration'; method = 'PATCH'; path = '/v1/system/proxy-configuration' },
    [pscustomobject]@{ operationId = 'getTask'; method = 'GET'; path = '/v1/tasks/{id}' },
    [pscustomobject]@{ operationId = 'setCeipStatus'; method = 'PATCH'; path = '/v1/system/ceip' }
)

Assert-Equal $contract.derived_from.repository_commit_sha $PinnedCommit 'Contract commit provenance changed'
Assert-Equal $contract.derived_from.spec_path $SpecPath 'Contract spec path changed'
Assert-Equal $contract.derived_from.license 'Apache-2.0' 'Contract license changed'
Assert-Equal $contract.derived_from.spec_version '9.1.0.0' 'Contract version changed'
Assert-Equal @($contract.operations).Count $expectedOperations.Count 'Contract must contain exactly the selected operations'
Assert-Equal @($sources.operations).Count $expectedOperations.Count 'Official sources must contain exactly the selected operations'

for ($index = 0; $index -lt $expectedOperations.Count; $index++) {
    $expected = $expectedOperations[$index]
    $actual = @($contract.operations)[$index]
    $sourceRecord = @($sources.operations)[$index]
    Assert-Equal $actual.operationId $expected.operationId "Contract operationId at index $index"
    Assert-Equal $actual.method $expected.method "Contract method for $($expected.operationId)"
    Assert-Equal $actual.path $expected.path "Contract path for $($expected.operationId)"
    Assert-Equal $sourceRecord.operationId $expected.operationId "Official source operationId at index $index"
    Assert-Equal $sourceRecord.method $expected.method "Official source method for $($expected.operationId)"
    Assert-Equal $sourceRecord.path $expected.path "Official source path for $($expected.operationId)"
    Assert-Equal $sourceRecord.repository_commit_sha $PinnedCommit "Official source commit for $($expected.operationId)"
    Assert-Equal $sourceRecord.spec_path $SpecPath "Official source spec path for $($expected.operationId)"
    Assert-True ($sourceRecord.source_url -ceq "https://github.com/vmware/vcf-api-specs/blob/$PinnedCommit/$SpecPath") "Official source URL for $($expected.operationId) must point to the pinned specification"
}

Assert-JsonMemberSet $contract.schemas.SystemUpdateSpec.properties @('maxAllowedDomainsInSubscription') 'SystemUpdateSpec properties changed'
Assert-Equal (@($contract.schemas.SystemUpdateSpec.required) -join ',') 'maxAllowedDomainsInSubscription' 'SystemUpdateSpec required fields changed'
Assert-JsonMemberSet $contract.schemas.ProxyConfiguration.properties @('isConfigured', 'isEnabled', 'host', 'port', 'transferProtocol', 'username', 'password', 'isAuthenticated') 'ProxyConfiguration properties changed'
Assert-True ([bool]$contract.schemas.ProxyConfiguration.properties.isConfigured.readOnly) 'isConfigured must remain read-only'
Assert-JsonMemberSet $contract.schemas.CeipUpdateSpec.properties @('status') 'CeipUpdateSpec properties changed'
Assert-Equal (@($contract.schemas.CeipUpdateSpec.required) -join ',') 'status' 'CeipUpdateSpec required fields changed'

$availableSdk = @(Get-Module -ListAvailable -Name VMware.Sdk.Vcf.Installer | Where-Object { $_.Version -ge $MinimumSdkVersion } | Sort-Object Version -Descending)
Assert-True ($availableSdk.Count -gt 0) "VMware.Sdk.Vcf.Installer $MinimumSdkVersion or newer was not provided by the environment"

Import-Module -Name $ModulePath -Force
$publicFunctions = @(Get-Command -Module VcfInstallerChange -CommandType Function | Select-Object -ExpandProperty Name | Sort-Object)
Assert-Equal ($publicFunctions -join ',') 'Connect-VcfInstallerChangeServer,Invoke-VcfInstallerChange' 'Exported function set is incorrect'

$connectCommand = Get-Command Connect-VcfInstallerChangeServer
$invokeCommand = Get-Command Invoke-VcfInstallerChange
foreach ($parameterName in @('Server', 'Credential', 'Port', 'Protocol')) {
    Assert-True $connectCommand.Parameters.ContainsKey($parameterName) "Connect function is missing parameter $parameterName"
}
foreach ($parameterName in @('Connection', 'MaxAllowedDomainsInSubscription', 'ProxyHost', 'ProxyPort', 'ProxyProtocol', 'CeipStatus', 'PollIntervalSeconds', 'TaskTimeoutSeconds')) {
    Assert-True $invokeCommand.Parameters.ContainsKey($parameterName) "Invoke function is missing parameter $parameterName"
}

$runDirectory = Join-Path ([IO.Path]::GetTempPath()) ("vcf91-0201-" + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $runDirectory
$requestLog = Join-Path $runDirectory 'requests.jsonl'
$readyFile = Join-Path $runDirectory 'ready.txt'
$stdoutFile = Join-Path $runDirectory 'mock.stdout'
$stderrFile = Join-Path $runDirectory 'mock.stderr'
$mockProcess = $null
$result = $null
$systemFailureResult = $null
$proxyApiFailureResult = $null
$proxyTaskFailureResult = $null
$successResult = $null
$timeoutResult = $null

try {
try {
    $mockProcess = Start-Process -FilePath 'python3' -ArgumentList @(
        $MockPath,
        '--contract', $ContractPath,
        '--log', $requestLog,
        '--ready', $readyFile
    ) -PassThru -NoNewWindow -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile

    $readyDeadline = [datetime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path -LiteralPath $readyFile -PathType Leaf)) {
        if ($mockProcess.HasExited) {
            $mockError = if (Test-Path -LiteralPath $stderrFile) { Get-Content -LiteralPath $stderrFile -Raw } else { '' }
            throw "Loopback mock exited before becoming ready: $mockError"
        }
        if ([datetime]::UtcNow -ge $readyDeadline) {
            throw 'Timed out waiting for the loopback mock to become ready.'
        }
        Start-Sleep -Milliseconds 50
    }

    $port = [int](Get-Content -LiteralPath $readyFile -Raw)
    $securePassword = ConvertTo-SecureString 'Moonshiner-Loopback-42!' -AsPlainText -Force
    $credential = [pscredential]::new('operator@local', $securePassword)
    $connection = Connect-VcfInstallerChangeServer `
        -Server '127.0.0.1' `
        -Credential $credential `
        -Port $port `
        -Protocol 'http'

    Assert-True ($null -ne $connection) 'Connect-VcfInstallerChangeServer returned no connection'
    $defaultConnections = @(
        Get-Variable -Name defaultInstallerConnections -Scope Global -ValueOnly -ErrorAction SilentlyContinue
    )
    Assert-True (-not ($defaultConnections -contains $connection)) 'The connection must be non-default'

    $result = Invoke-VcfInstallerChange `
        -Connection $connection `
        -MaxAllowedDomainsInSubscription 8 `
        -ProxyHost 'proxy.corp.local' `
        -ProxyPort 8443 `
        -ProxyProtocol 'HTTPS' `
        -CeipStatus 'DISABLE' `
        -PollIntervalSeconds 0 `
        -TaskTimeoutSeconds 5

    $systemFailureResult = Invoke-VcfInstallerChange `
        -Connection $connection `
        -MaxAllowedDomainsInSubscription 13 `
        -ProxyHost 'must-not-run.local' `
        -ProxyPort 8001 `
        -PollIntervalSeconds 0 `
        -TaskTimeoutSeconds 5

    $proxyApiFailureResult = Invoke-VcfInstallerChange `
        -Connection $connection `
        -MaxAllowedDomainsInSubscription 9 `
        -ProxyHost 'proxy-api-fail.local' `
        -ProxyPort 8002 `
        -ProxyProtocol 'HTTP' `
        -CeipStatus 'ENABLE' `
        -PollIntervalSeconds 0 `
        -TaskTimeoutSeconds 5

    $proxyTaskFailureResult = Invoke-VcfInstallerChange `
        -Connection $connection `
        -MaxAllowedDomainsInSubscription 10 `
        -ProxyHost 'proxy-task-fail.local' `
        -ProxyPort 8003 `
        -ProxyProtocol 'HTTPS' `
        -CeipStatus 'ENABLE' `
        -PollIntervalSeconds 0 `
        -TaskTimeoutSeconds 5

    $successResult = Invoke-VcfInstallerChange `
        -Connection $connection `
        -MaxAllowedDomainsInSubscription 11 `
        -ProxyHost 'proxy-success.local' `
        -ProxyPort 8004 `
        -ProxyProtocol 'HTTP' `
        -CeipStatus 'ENABLE' `
        -PollIntervalSeconds 0 `
        -TaskTimeoutSeconds 5

    $timeoutResult = Invoke-VcfInstallerChange `
        -Connection $connection `
        -MaxAllowedDomainsInSubscription 12 `
        -ProxyHost 'proxy-timeout.local' `
        -ProxyPort 8005 `
        -ProxyProtocol 'HTTPS' `
        -CeipStatus 'ENABLE' `
        -PollIntervalSeconds 1 `
        -TaskTimeoutSeconds 1
}
finally {
    if ($null -ne $mockProcess -and -not $mockProcess.HasExited) {
        Stop-Process -Id $mockProcess.Id -Force
        $mockProcess.WaitForExit()
    }
}

    Assert-True ($null -ne $result) 'Invoke-VcfInstallerChange returned no report'
    Assert-True ($result -isnot [array]) 'Invoke-VcfInstallerChange must return one report object'
    Assert-PropertyOrder $result @('Outcome', 'Steps') 'Report property order is incorrect'
    Assert-Equal $result.Outcome 'PartialFailure' 'Overall outcome must preserve the late failure'

    $steps = @($result.Steps)
    Assert-Equal $steps.Count 3 'Report must always contain three step records'
    $stepPropertyOrder = @('Name', 'OperationId', 'Status', 'TaskId', 'TaskStatus', 'ErrorCode', 'ErrorMessage')
    foreach ($step in $steps) {
        Assert-PropertyOrder $step $stepPropertyOrder "Step property order for $($step.Name) is incorrect"
    }

    Assert-Equal $steps[0].Name 'SystemConfiguration' 'First step name'
    Assert-Equal $steps[0].OperationId 'updateSystemConfiguration' 'First step operationId'
    Assert-Equal $steps[0].Status 'Succeeded' 'First step status'
    Assert-Equal $steps[0].TaskId $null 'Synchronous system step task id'
    Assert-Equal $steps[0].TaskStatus $null 'Synchronous system step task status'
    Assert-Equal $steps[0].ErrorCode $null 'Successful system step error code'
    Assert-Equal $steps[0].ErrorMessage $null 'Successful system step error message'

    Assert-Equal $steps[1].Name 'ProxyConfiguration' 'Second step name'
    Assert-Equal $steps[1].OperationId 'updateProxyConfiguration' 'Second step operationId'
    Assert-Equal $steps[1].Status 'Succeeded' 'Second step status'
    Assert-Equal $steps[1].TaskId 'task-proxy-001' 'Proxy task id'
    Assert-Equal $steps[1].TaskStatus 'SUCCESSFUL' 'Proxy terminal task status'
    Assert-Equal $steps[1].ErrorCode $null 'Successful proxy step error code'
    Assert-Equal $steps[1].ErrorMessage $null 'Successful proxy step error message'

    Assert-Equal $steps[2].Name 'Ceip' 'Third step name'
    Assert-Equal $steps[2].OperationId 'setCeipStatus' 'Third step operationId'
    Assert-Equal $steps[2].Status 'Failed' 'Third step status'
    Assert-Equal $steps[2].TaskId $null 'Rejected CEIP step task id'
    Assert-Equal $steps[2].TaskStatus $null 'Rejected CEIP step task status'
    Assert-Equal $steps[2].ErrorCode 'CEIP_CHANGE_CONFLICT' 'CEIP API error code'
    Assert-Equal $steps[2].ErrorMessage 'CEIP is locked by the compliance policy.' 'CEIP API error message'

    Assert-ReportShape $systemFailureResult 'Failed' 'System API failure'
    $systemFailureSteps = @($systemFailureResult.Steps)
    Assert-Step $systemFailureSteps[0] 'SystemConfiguration' 'updateSystemConfiguration' 'Failed' $null $null 'SYSTEM_LIMIT_REJECTED' 'The requested domain limit is not allowed.' 'System API failure step 1'
    Assert-Step $systemFailureSteps[1] 'ProxyConfiguration' 'updateProxyConfiguration' 'NotRun' $null $null $null $null 'System API failure step 2'
    Assert-Step $systemFailureSteps[2] 'Ceip' 'setCeipStatus' 'NotRun' $null $null $null $null 'System API failure step 3'

    Assert-ReportShape $proxyApiFailureResult 'PartialFailure' 'Proxy API failure'
    $proxyApiFailureSteps = @($proxyApiFailureResult.Steps)
    Assert-Step $proxyApiFailureSteps[0] 'SystemConfiguration' 'updateSystemConfiguration' 'Succeeded' $null $null $null $null 'Proxy API failure step 1'
    Assert-Step $proxyApiFailureSteps[1] 'ProxyConfiguration' 'updateProxyConfiguration' 'Failed' $null $null 'PROXY_API_UNAVAILABLE' 'The proxy service is temporarily unavailable.' 'Proxy API failure step 2'
    Assert-Step $proxyApiFailureSteps[2] 'Ceip' 'setCeipStatus' 'NotRun' $null $null $null $null 'Proxy API failure step 3'

    Assert-ReportShape $proxyTaskFailureResult 'PartialFailure' 'Proxy task failure'
    $proxyTaskFailureSteps = @($proxyTaskFailureResult.Steps)
    Assert-Step $proxyTaskFailureSteps[0] 'SystemConfiguration' 'updateSystemConfiguration' 'Succeeded' $null $null $null $null 'Proxy task failure step 1'
    Assert-Step $proxyTaskFailureSteps[1] 'ProxyConfiguration' 'updateProxyConfiguration' 'Failed' 'task-proxy-failed' 'FAILED' 'PROXY_TASK_FAILED' 'The proxy endpoint could not be reached.' 'Proxy task failure step 2'
    Assert-Step $proxyTaskFailureSteps[2] 'Ceip' 'setCeipStatus' 'NotRun' $null $null $null $null 'Proxy task failure step 3'

    Assert-ReportShape $successResult 'Succeeded' 'Successful workflow'
    $successSteps = @($successResult.Steps)
    Assert-Step $successSteps[0] 'SystemConfiguration' 'updateSystemConfiguration' 'Succeeded' $null $null $null $null 'Successful workflow step 1'
    Assert-Step $successSteps[1] 'ProxyConfiguration' 'updateProxyConfiguration' 'Succeeded' 'task-proxy-success' 'SUCCESSFUL' $null $null 'Successful workflow step 2'
    Assert-Step $successSteps[2] 'Ceip' 'setCeipStatus' 'Succeeded' $null $null $null $null 'Successful workflow step 3'

    Assert-ReportShape $timeoutResult 'PartialFailure' 'Proxy task timeout'
    $timeoutSteps = @($timeoutResult.Steps)
    Assert-Step $timeoutSteps[0] 'SystemConfiguration' 'updateSystemConfiguration' 'Succeeded' $null $null $null $null 'Proxy task timeout step 1'
    Assert-Step $timeoutSteps[1] 'ProxyConfiguration' 'updateProxyConfiguration' 'Failed' 'task-proxy-timeout' 'IN_PROGRESS' 'TASK_TIMEOUT' 'Proxy task task-proxy-timeout did not finish within 1 seconds.' 'Proxy task timeout step 2'
    Assert-Step $timeoutSteps[2] 'Ceip' 'setCeipStatus' 'NotRun' $null $null $null $null 'Proxy task timeout step 3'

    Assert-True (Test-Path -LiteralPath $requestLog -PathType Leaf) 'Loopback request log was not created'
    $requests = @(
        Get-Content -LiteralPath $requestLog |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json -Depth 30 }
    )
    $expectedPrefix = @(
        [pscustomobject]@{ operationId = 'createToken'; method = 'POST'; path = '/v1/tokens' },
        [pscustomobject]@{ operationId = 'sdkConnectionProbe'; method = 'GET'; path = $SdkConnectionProbePath },
        [pscustomobject]@{ operationId = 'updateSystemConfiguration'; method = 'PATCH'; path = '/v1/system' },
        [pscustomobject]@{ operationId = 'updateProxyConfiguration'; method = 'PATCH'; path = '/v1/system/proxy-configuration' },
        [pscustomobject]@{ operationId = 'getTask'; method = 'GET'; path = '/v1/tasks/task-proxy-001' },
        [pscustomobject]@{ operationId = 'setCeipStatus'; method = 'PATCH'; path = '/v1/system/ceip' },
        [pscustomobject]@{ operationId = 'updateSystemConfiguration'; method = 'PATCH'; path = '/v1/system' },
        [pscustomobject]@{ operationId = 'updateSystemConfiguration'; method = 'PATCH'; path = '/v1/system' },
        [pscustomobject]@{ operationId = 'updateProxyConfiguration'; method = 'PATCH'; path = '/v1/system/proxy-configuration' },
        [pscustomobject]@{ operationId = 'updateSystemConfiguration'; method = 'PATCH'; path = '/v1/system' },
        [pscustomobject]@{ operationId = 'updateProxyConfiguration'; method = 'PATCH'; path = '/v1/system/proxy-configuration' },
        [pscustomobject]@{ operationId = 'getTask'; method = 'GET'; path = '/v1/tasks/task-proxy-failed' },
        [pscustomobject]@{ operationId = 'updateSystemConfiguration'; method = 'PATCH'; path = '/v1/system' },
        [pscustomobject]@{ operationId = 'updateProxyConfiguration'; method = 'PATCH'; path = '/v1/system/proxy-configuration' },
        [pscustomobject]@{ operationId = 'getTask'; method = 'GET'; path = '/v1/tasks/task-proxy-success' },
        [pscustomobject]@{ operationId = 'setCeipStatus'; method = 'PATCH'; path = '/v1/system/ceip' },
        [pscustomobject]@{ operationId = 'updateSystemConfiguration'; method = 'PATCH'; path = '/v1/system' },
        [pscustomobject]@{ operationId = 'updateProxyConfiguration'; method = 'PATCH'; path = '/v1/system/proxy-configuration' }
    )
    Assert-True ($requests.Count -gt $expectedPrefix.Count) 'The timeout scenario must poll its task at least once'

    for ($index = 0; $index -lt $expectedPrefix.Count; $index++) {
        $request = $requests[$index]
        $expected = $expectedPrefix[$index]
        Assert-Equal $request.sequence ($index + 1) "Request sequence number at index $index"
        Assert-Equal $request.operationId $expected.operationId "Operation at index $index"
        Assert-Equal $request.method $expected.method "HTTP method at index $index"
        Assert-Equal $request.path $expected.path "HTTP path at index $index"
        Assert-Equal $request.query '' "Query string at index $index must be empty"
        if ($expected.operationId -eq 'createToken') {
            Assert-True ([string]::IsNullOrEmpty($request.headers.authorization)) 'Token creation must not send an Authorization header'
        }
        else {
            Assert-Equal $request.headers.authorization 'Bearer loopback-access-token' "Bearer authorization at index $index"
        }
    }

    for ($index = $expectedPrefix.Count; $index -lt $requests.Count; $index++) {
        $request = $requests[$index]
        Assert-Equal $request.sequence ($index + 1) "Timeout request sequence number at index $index"
        Assert-Equal $request.operationId 'getTask' "Timeout operation at index $index"
        Assert-Equal $request.method 'GET' "Timeout method at index $index"
        Assert-Equal $request.path '/v1/tasks/task-proxy-timeout' "Timeout path at index $index"
        Assert-Equal $request.query '' "Timeout query string at index $index"
        Assert-Equal $request.headers.authorization 'Bearer loopback-access-token' "Timeout authorization at index $index"
        Assert-Equal $request.bodyText '' "Timeout task GET body at index $index"
    }

    $tokenRequest = $requests[0]
    Assert-True ($tokenRequest.headers.'content-type' -match '^application/json(?:\s*;.*)?$') 'Token request content type'
    Assert-JsonMemberSet $tokenRequest.json @('username', 'password') 'Unset token alternatives must be omitted'
    Assert-Equal $tokenRequest.json.username 'operator@local' 'Token username'
    Assert-Equal $tokenRequest.json.password 'Moonshiner-Loopback-42!' 'Token password'

    Assert-Equal $requests[1].bodyText '' 'SDK connection probe must not send a body'

    Assert-SystemRequest $requests[2] 8 'Late CEIP failure system request'
    Assert-ProxyRequest $requests[3] 'proxy.corp.local' 8443 'HTTPS' 'Late CEIP failure proxy request'
    Assert-Equal $requests[4].pathParameters.id 'task-proxy-001' 'Late CEIP failure task id'
    Assert-Equal $requests[4].bodyText '' 'Late CEIP failure task GET body'
    Assert-JsonMemberSet $requests[5].json @('status') 'Late CEIP failure CEIP request member set'
    Assert-Equal $requests[5].json.status 'DISABLE' 'Late CEIP failure status'

    Assert-SystemRequest $requests[6] 13 'System failure request'
    Assert-SystemRequest $requests[7] 9 'Proxy API failure system request'
    Assert-ProxyRequest $requests[8] 'proxy-api-fail.local' 8002 'HTTP' 'Proxy API failure request'

    Assert-SystemRequest $requests[9] 10 'Proxy task failure system request'
    Assert-ProxyRequest $requests[10] 'proxy-task-fail.local' 8003 'HTTPS' 'Proxy task failure request'
    Assert-Equal $requests[11].pathParameters.id 'task-proxy-failed' 'Proxy failed task id'
    Assert-Equal $requests[11].bodyText '' 'Proxy failed task GET body'

    Assert-SystemRequest $requests[12] 11 'Successful workflow system request'
    Assert-ProxyRequest $requests[13] 'proxy-success.local' 8004 'HTTP' 'Successful workflow proxy request'
    Assert-Equal $requests[14].pathParameters.id 'task-proxy-success' 'Successful proxy task id'
    Assert-Equal $requests[14].bodyText '' 'Successful proxy task GET body'
    Assert-JsonMemberSet $requests[15].json @('status') 'Successful CEIP request member set'
    Assert-Equal $requests[15].json.status 'ENABLE' 'Successful CEIP status'

    Assert-SystemRequest $requests[16] 12 'Timeout workflow system request'
    Assert-ProxyRequest $requests[17] 'proxy-timeout.local' 8005 'HTTPS' 'Timeout workflow proxy request'

    foreach ($request in @($requests | Where-Object { $_.method -in @('POST', 'PATCH') })) {
        Assert-True ($request.headers.'content-type' -match '^application/json(?:\s*;.*)?$') "JSON content type for request sequence $($request.sequence)"
    }

    Write-Output 'PASS: VCF Installer workflow outcomes, stop semantics, exact wire contract, task polling, omission semantics, and error reporting are correct.'
}
finally {
    Remove-Module VcfInstallerChange -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $runDirectory) {
        Remove-Item -LiteralPath $runDirectory -Recurse -Force
    }
}
