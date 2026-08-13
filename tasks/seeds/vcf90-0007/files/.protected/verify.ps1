# Protected acceptance harness for VcfCredentialRotation.
# It drives the genuine VMware.Sdk.Vcf.SddcManager cmdlets against a
# contract-pinned loopback SDDC Manager and replays the recorded wire history.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'
[System.Globalization.CultureInfo]::CurrentCulture =
    [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture =
    [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

$script:Checks = 0
$script:Failures = 0

function Assert-True {
    param([string] $Label, [bool] $Condition)
    $script:Checks++
    if ($Condition) { return }
    $script:Failures++
    Write-Output "FAIL $Label"
}

function Assert-Eq {
    param([string] $Label, $Expected, $Actual)
    $script:Checks++
    if ("$Expected" -ceq "$Actual") { return }
    $script:Failures++
    Write-Output "FAIL $Label"
    Write-Output "  expected: $Expected"
    Write-Output "  actual:   $Actual"
}

function Get-JsonMemberNames {
    param([Parameter(Mandatory)] [object] $InputObject)
    @($InputObject.PSObject.Properties.Name | Sort-Object)
}

$Root = Split-Path -Parent $PSScriptRoot
$ModulePath = Join-Path $Root 'VcfCredentialRotation/VcfCredentialRotation.psm1'
$ContractPath = Join-Path $Root 'docs/contract.json'
$SourcesPath = Join-Path $Root 'docs/official_sources.json'
$MockPath = Join-Path $PSScriptRoot 'mock_sddc_credentials.py'

if (-not (Test-Path -LiteralPath $ModulePath -PathType Leaf)) {
    Write-Output 'FAIL VcfCredentialRotation/VcfCredentialRotation.psm1 not found'
    exit 1
}

# PowerCLI is an environment prerequisite, never a fixture supplied by this seed.
$sdk = Get-Module -ListAvailable -Name 'VMware.Sdk.Vcf.SddcManager' |
    Where-Object { $_.Version -ge [version] '13.5.0.25380678' } |
    Sort-Object Version -Descending |
    Select-Object -First 1
if ($null -eq $sdk) {
    Write-Output (
        'FAIL prerequisite VMware.Sdk.Vcf.SddcManager >= 13.5.0.25380678 ' +
        'is not installed'
    )
    exit 1
}

$source = Get-Content -LiteralPath $ModulePath -Raw
foreach ($forbidden in @(
    '\bInvoke-WebRequest\b',
    '\bInvoke-RestMethod\b',
    '\bSystem\.Net\.(Http|Sockets)\b',
    '\bHttpClient\b',
    '\bWebRequest\b',
    '\bWebClient\b',
    '\bSocket\b',
    '\bTcpClient\b',
    '\bUdpClient\b',
    '\bStart-Process\b',
    '\bcurl\b',
    '\bwget\b'
)) {
    Assert-True "solution does not bypass the VMware SDK with $forbidden" (
        $source -notmatch $forbidden
    )
}

$parseTokens = $null
$parseErrors = $null
$solutionAst = [System.Management.Automation.Language.Parser]::ParseFile(
    $ModulePath,
    [ref] $parseTokens,
    [ref] $parseErrors
)
Assert-Eq 'solution parses without PowerShell syntax errors' 0 @($parseErrors).Count
$rotationFunctions = @(
    $solutionAst.FindAll(
        {
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -ceq 'Invoke-VcfCredentialRotation'
        },
        $true
    )
)
Assert-Eq 'solution defines Invoke-VcfCredentialRotation exactly once' 1 `
    $rotationFunctions.Count
if ($rotationFunctions.Count -eq 1) {
    $paramBlock = $rotationFunctions[0].Body.ParamBlock
    $parameters = @(
        if ($null -ne $paramBlock) { $paramBlock.Parameters }
    )
    Assert-Eq 'function keeps the supplied parameter names and order' (
        'Server,ResourceType,ResourceName,Username,CredentialType,AccountType,' +
        'DrainAction,PublishAction,DrainLimit,DrainIntervalSeconds,PollLimit,' +
        'PollIntervalSeconds,SleepAction'
    ) (($parameters.Name.VariablePath.UserPath) -join ',')
    $expectedDefaults = [ordered]@{
        DrainLimit           = '10'
        DrainIntervalSeconds = '2'
        PollLimit            = '60'
        PollIntervalSeconds  = '5'
    }
    foreach ($entry in $expectedDefaults.GetEnumerator()) {
        $parameter = @(
            $parameters | Where-Object {
                $_.Name.VariablePath.UserPath -ceq $entry.Key
            }
        )
        Assert-Eq "$($entry.Key) appears exactly once" 1 $parameter.Count
        if ($parameter.Count -eq 1) {
            $actualDefault = '<missing>'
            if ($null -ne $parameter[0].DefaultValue) {
                $actualDefault = $parameter[0].DefaultValue.Extent.Text
            }
            Assert-Eq "$($entry.Key) keeps its supplied default" $entry.Value `
                $actualDefault
        }
    }
}

$commandAsts = @(
    $solutionAst.FindAll(
        {
            param($node)
            $node -is [System.Management.Automation.Language.CommandAst]
        },
        $true
    )
)
$solutionCommandNames = @(
    $commandAsts | ForEach-Object { $_.GetCommandName() } |
        Where-Object { $null -ne $_ }
)
$requiredSdkCommands = @(
    'Initialize-VcfBaseCredential',
    'Initialize-VcfResourceCredentials',
    'Initialize-VcfCredentialsUpdateSpec',
    'Invoke-VcfGetCredentials',
    'Invoke-VcfUpdateOrRotatePasswords',
    'Invoke-VcfGetCredentialsTask',
    'Invoke-VcfRetryCredentialsTask',
    'Invoke-VcfCancelCredentialsTask',
    'Invoke-VcfGetCredential'
)
foreach ($required in $requiredSdkCommands) {
    Assert-True "solution contains an invocation of SDK cmdlet $required" (
        $solutionCommandNames -ccontains $required
    )
    $shadowingDefinition = @(
        $solutionAst.FindAll(
            {
                param($node)
                $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                    $node.Name -ceq $required
            },
            $true
        )
    )
    Assert-Eq "solution does not shadow SDK cmdlet $required" 0 `
        $shadowingDefinition.Count
}
$vendored = @(
    Get-ChildItem -LiteralPath $Root -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Extension.ToLowerInvariant() -in @('.dll', '.nupkg', '.snupkg', '.zip')
        }
)
Assert-Eq 'solution does not vendor binary dependencies' 0 $vendored.Count

# The projection and the loopback service are protected inputs.
$expectedProtectedHashes = [ordered]@{
    $ContractPath = 'e63883492645bf00998b8a604f4063d3bc5d35b89995475603691276fb751a70'
    $SourcesPath  = 'd9d19f5b29ba3a7f8e46331e301287963e379ee0f986b3a4da8ffcbbcf356cd8'
    $MockPath     = '8148a63c1fa16107de12ead48fb95d5088d6bf2832d5484e5a5316bf5d6e1f26'
}
foreach ($entry in $expectedProtectedHashes.GetEnumerator()) {
    $actualHash = (Get-FileHash -LiteralPath $entry.Key -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-Eq "protected file hash $([IO.Path]::GetFileName($entry.Key))" `
        $entry.Value $actualHash
}

$contract = Get-Content -LiteralPath $ContractPath -Raw | ConvertFrom-Json
$sources = Get-Content -LiteralPath $SourcesPath -Raw | ConvertFrom-Json
$expectedSha = '85151f6b1bb58f13b6ac0304bfec53904bea085f'
$expectedBlob = 'ff648cdf010715649b607f0edfa480b2c515e2a9'
$expectedSpecPath = 'specifications/sddc-manager/sddc-manager-openapi.json'
$expectedOps = (
    'createToken,getCredentials,updateOrRotatePasswords,getCredentialsTask,' +
    'retryCredentialsTask,cancelCredentialsTask,getCredential'
)
Assert-Eq 'contract pins OpenAPI 3.0.1' '3.0.1' $contract.source.openapiVersion
Assert-Eq 'contract pins the VCF 9.0 specification' '9.0.0.0' $contract.source.apiVersion
Assert-Eq 'contract pins the 9.0.0.0 tag' '9.0.0.0' $contract.source.tag
Assert-Eq 'contract commit sha' $expectedSha $contract.source.commitSha
Assert-True 'contract is not the 9.1 revision of the same file' (
    $contract.source.commitSha -cne '3949fc33339fc5ea1b77eadb258f1cf49aa88e26'
)
Assert-Eq 'contract spec path' $expectedSpecPath $contract.source.specPath
Assert-Eq 'contract spec blob sha' $expectedBlob $contract.source.specBlobSha
Assert-Eq 'contract operationIds' $expectedOps (($contract.operations.operationId) -join ',')
Assert-Eq 'contract operation methods' 'POST,GET,PATCH,GET,PATCH,DELETE,GET' (
    ($contract.operations.method) -join ','
)
Assert-Eq 'contract operation paths' (
    '/v1/tokens,/v1/credentials,/v1/credentials,/v1/credentials/tasks/{id},' +
    '/v1/credentials/tasks/{id},/v1/credentials/tasks/{id},/v1/credentials/{id}'
) (($contract.operations.path) -join ',')
Assert-Eq 'CredentialsUpdateSpec required fields match the specification' (
    'elements,operationType'
) (($contract.schemas.CredentialsUpdateSpec.required) -join ',')
Assert-Eq 'BaseCredential projected property order' (
    'credentialType,accountType,username,password'
) (($contract.schemas.BaseCredential.properties.PSObject.Properties.Name) -join ',')
Assert-Eq 'BaseCredential requires only the username' 'username' (
    ($contract.schemas.BaseCredential.required) -join ','
)
Assert-Eq 'contract declares the SDK connection probe outside the specification' `
    '/v1/sddc-manager' $contract.sdkConnectionProbe.path
Assert-True 'the SDK connection probe carries no operationId' (
    $null -eq $contract.sdkConnectionProbe.operationId
)
Assert-Eq 'official source license' 'Apache-2.0' $sources.license
Assert-Eq 'official source commit sha' $expectedSha $sources.specification.repository_commit_sha
Assert-Eq 'official source repository tag' '9.0.0.0' $sources.specification.repository_tag
Assert-Eq 'official source spec path' $expectedSpecPath $sources.specification.spec_path
Assert-Eq 'official source spec blob sha' $expectedBlob $sources.specification.spec_blob_sha
Assert-Eq 'official source operationIds' $expectedOps (($sources.operations.operationId) -join ',')
foreach ($entry in $sources.operations) {
    Assert-Eq "source $($entry.operationId) repeats the commit" $expectedSha `
        $entry.repository_commit_sha
    Assert-Eq "source $($entry.operationId) repeats the spec path" $expectedSpecPath `
        $entry.spec_path
}

Import-Module 'VMware.Sdk.Vcf.SddcManager' -MinimumVersion '13.5.0.25380678' -Force
Import-Module $ModulePath -Force
$exports = @(
    Get-Command -Module 'VcfCredentialRotation' -CommandType Function |
        Select-Object -ExpandProperty Name
)
Assert-Eq 'module exports exactly one function' 'Invoke-VcfCredentialRotation' (
    $exports -join ','
)
foreach ($commandName in $requiredSdkCommands) {
    $command = Get-Command $commandName -ErrorAction SilentlyContinue
    Assert-Eq "$commandName comes from the genuine SDK" 'VMware.Sdk.Vcf.SddcManager' (
        $(if ($null -eq $command) { 'missing' } else { $command.Source })
    )
}

# Command breakpoints observe the real commands without replacing them. This
# proves the dynamic scenarios execute every required SDK command, including
# the three model initializers whose effects alone are not visible in the wire log.
$global:Vcf90SdkCommandHits = @{}
$sdkBreakpoints = @(
    foreach ($commandName in $requiredSdkCommands) {
        $global:Vcf90SdkCommandHits[$commandName] = 0
        $action = [scriptblock]::Create(
            '$global:Vcf90SdkCommandHits[''' + $commandName + ''']++'
        )
        Set-PSBreakpoint -Command $commandName -Action $action
    }
)

$loginUser = 'svc-vcf-rotation'
$loginPassword = 'dummy-vcf-login-pass-90'
$accessToken = 'dummy-vcf-access-token-90'
$secrets = @(
    'dummy-old-secret-a-90', 'dummy-rotated-secret-a-90',
    'dummy-old-secret-b-90', 'dummy-rotated-secret-b-90',
    'dummy-old-secret-c-90', 'dummy-rotated-secret-c-90',
    'dummy-old-secret-d-90', 'dummy-rotated-secret-d-90',
    'dummy-old-secret-e-90', 'dummy-rotated-secret-e-90',
    'dummy-old-secret-f-90', 'dummy-rotated-secret-f-90',
    'dummy-old-secret-g-90', 'dummy-rotated-secret-g-90',
    'dummy-old-secret-h-90', 'dummy-rotated-secret-h-90',
    'dummy-old-secret-i-90',
    'dummy-old-secret-j-90', 'dummy-rotated-secret-j-90',
    'dummy-old-secret-k-90', 'dummy-rotated-secret-k-90',
    'dummy-old-secret-l-90', 'dummy-rotated-secret-l-90',
    $loginPassword, $accessToken
)

$successResource = 'vc-a.rainpole.io'
$retryResource = 'nsx-b.rainpole.io'
$cancelResource = 'esx-c.rainpole.io'
$drainResource = 'vc-d.rainpole.io'
$successCredential = 'c1000001-0001-4001-8001-000000000001'
$retryCredential = 'c2000002-0002-4002-8002-000000000002'
$cancelCredential = 'c3000003-0003-4003-8003-000000000003'
$successTask = 'a1000001-0001-4001-8001-000000000001'
$retryTask = 'a2000002-0002-4002-8002-000000000002'
$cancelTask = 'a3000003-0003-4003-8003-000000000003'

$scratch = Join-Path ([IO.Path]::GetTempPath()) (
    'vcf90-0007-' + [guid]::NewGuid().ToString('N')
)
New-Item -ItemType Directory -Force -Path $scratch > $null
$portFile = Join-Path $scratch 'port.txt'
$requestLog = Join-Path $scratch 'requests.jsonl'
$serverOut = Join-Path $scratch 'server.out'
$serverErr = Join-Path $scratch 'server.err'

function Get-RequestLog {
    if (-not (Test-Path -LiteralPath $script:RequestLogPath -PathType Leaf)) { return @() }
    @(
        Get-Content -LiteralPath $script:RequestLogPath |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
}

function Get-RotationSubmissionCount {
    @(Get-RequestLog | Where-Object { $_.operationId -ceq 'updateOrRotatePasswords' }).Count
}

$script:RequestLogPath = $requestLog
$serverProcess = $null
try {
    $serverProcess = Start-Process -FilePath 'python3' `
        -ArgumentList @($MockPath, $portFile, $requestLog) `
        -PassThru `
        -RedirectStandardOutput $serverOut `
        -RedirectStandardError $serverErr
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Path -LiteralPath $portFile -PathType Leaf)) {
        if ($serverProcess.HasExited -or [DateTime]::UtcNow -gt $deadline) {
            $detail = Get-Content -LiteralPath $serverErr -Raw -ErrorAction SilentlyContinue
            throw "loopback mock failed to start: $detail"
        }
        Start-Sleep -Milliseconds 40
    }
    $port = [int] (Get-Content -LiteralPath $portFile -Raw).Trim()

    $secureLogin = ConvertTo-SecureString $loginPassword -AsPlainText -Force
    $connection = Connect-VcfSddcManagerServer `
        -Server '127.0.0.1' `
        -Port $port `
        -Protocol 'http' `
        -Credential ([pscredential]::new($loginUser, $secureLogin)) `
        -NotDefault
    $connection = @($connection)[0]
    Assert-True 'the genuine SDK connected to the loopback service' ($null -ne $connection)

    # ---- validation: reject every invalid argument before any request ---
    $validationCases = @(
        [pscustomobject]@{
            Label = 'blank ResourceType'
            Overrides = @{ ResourceType = '   ' }
        },
        [pscustomobject]@{
            Label = 'blank ResourceName'
            Overrides = @{ ResourceName = "`t" }
        },
        [pscustomobject]@{
            Label = 'blank Username'
            Overrides = @{ Username = '  ' }
        },
        [pscustomobject]@{
            Label = 'blank CredentialType'
            Overrides = @{ CredentialType = "`r`n" }
        },
        [pscustomobject]@{
            Label = 'blank bound AccountType'
            Overrides = @{ AccountType = ' ' }
        },
        [pscustomobject]@{
            Label = 'DrainLimit below one'
            Overrides = @{ DrainLimit = 0 }
        },
        [pscustomobject]@{
            Label = 'PollLimit below one'
            Overrides = @{ PollLimit = 0 }
        },
        [pscustomobject]@{
            Label = 'negative DrainIntervalSeconds'
            Overrides = @{ DrainIntervalSeconds = -1 }
        },
        [pscustomobject]@{
            Label = 'negative PollIntervalSeconds'
            Overrides = @{ PollIntervalSeconds = -1 }
        }
    )
    foreach ($validationCase in $validationCases) {
        $script:ValidationDrainCalls = 0
        $script:ValidationPublishCalls = 0
        $script:ValidationSleepCalls = 0
        $validationArguments = @{
            Server               = $connection
            ResourceType         = 'VCENTER'
            ResourceName         = 'validation-only.rainpole.io'
            Username             = 'svc-validation@vsphere.local'
            CredentialType       = 'SSO'
            DrainAction          = { $script:ValidationDrainCalls++; 0 }
            PublishAction        = { param($secret) $script:ValidationPublishCalls++ }
            SleepAction          = { param($seconds) $script:ValidationSleepCalls++ }
        }
        foreach ($override in $validationCase.Overrides.GetEnumerator()) {
            $validationArguments[$override.Key] = $override.Value
        }
        $requestsBeforeValidation = @(Get-RequestLog).Count
        $validationFailure = $null
        try {
            Invoke-VcfCredentialRotation @validationArguments > $null
        }
        catch {
            $validationFailure = $_.Exception
        }
        Assert-True "$($validationCase.Label) is rejected" (
            $null -ne $validationFailure
        )
        Assert-Eq "$($validationCase.Label) is rejected before any request" `
            $requestsBeforeValidation @(Get-RequestLog).Count
        Assert-Eq "$($validationCase.Label) does not drain" 0 `
            $script:ValidationDrainCalls
        Assert-Eq "$($validationCase.Label) does not publish" 0 `
            $script:ValidationPublishCalls
        Assert-Eq "$($validationCase.Label) does not wait" 0 `
            $script:ValidationSleepCalls
    }

    # ---- scenario A: drain, rotate, confirm, publish --------------------
    $script:Sleeps = [System.Collections.Generic.List[int]]::new()
    $script:DrainObservations = [System.Collections.Generic.List[string]]::new()
    $script:PublishObservations = [System.Collections.Generic.List[string]]::new()
    $script:DrainSequence = @(2, 0)
    $rotationsBefore = Get-RotationSubmissionCount
    $result = Invoke-VcfCredentialRotation `
        -Server $connection `
        -ResourceType 'VCENTER' `
        -ResourceName $successResource `
        -Username 'svc-rotate-a@vsphere.local' `
        -CredentialType 'SSO' `
        -AccountType 'USER' `
        -DrainLimit 4 `
        -DrainIntervalSeconds 2 `
        -PollLimit 6 `
        -PollIntervalSeconds 7 `
        -SleepAction { param($seconds) $script:Sleeps.Add([int] $seconds) } `
        -DrainAction {
            param($attempt)
            $script:DrainObservations.Add(
                "$attempt/" + (Get-RotationSubmissionCount)
            )
            $script:DrainSequence[[Math]::Min($attempt - 1, $script:DrainSequence.Count - 1)]
        } `
        -PublishAction {
            param($secret)
            $log = @(Get-RequestLog)
            $last = if ($log.Count -gt 0) { $log[-1] } else { $null }
            $previous = if ($log.Count -gt 1) { $log[-2] } else { $null }
            $script:PublishObservations.Add(
                (
                    $secret + '|' +
                    $(if ($null -eq $last) { 'none' } else { "$($last.operationId):$($last.path)" }) + '|' +
                    $(if ($null -eq $previous) { 'none' } else { "$($previous.operationId)" })
                )
            )
        }

    Assert-Eq 'result property order' (
        'ResourceName,ResourceType,Username,CredentialId,TaskId,Status,' +
        'PollCount,DrainAttempts,RetryAttempted,RotatedAt'
    ) (($result.PSObject.Properties.Name) -join ',')
    Assert-Eq 'result resource name' $successResource $result.ResourceName
    Assert-Eq 'result resource type' 'VCENTER' $result.ResourceType
    Assert-Eq 'result username' 'svc-rotate-a@vsphere.local' $result.Username
    Assert-Eq 'result selects the credential that matches account and type' `
        $successCredential $result.CredentialId
    Assert-Eq 'result task id' $successTask $result.TaskId
    Assert-Eq 'result preserves the terminal task status' 'SUCCESSFUL' $result.Status
    Assert-Eq 'result counts credentials-task reads' 3 $result.PollCount
    Assert-Eq 'result counts drain attempts' 2 $result.DrainAttempts
    Assert-Eq 'result records that no retry was needed' 'False' $result.RetryAttempted
    Assert-Eq 'result carries the rotated modification timestamp' `
        '2026-04-02T09:15:30.000Z' $result.RotatedAt
    foreach ($secret in $secrets) {
        Assert-True 'result never carries a secret' (
            (($result.PSObject.Properties.Value) -join '|') -notlike "*$secret*"
        )
    }
    Assert-Eq 'drain runs until it reports zero in-flight callers' 2 `
        $script:DrainObservations.Count
    Assert-Eq 'no rotation is submitted while callers hold the old secret' (
        "1/$rotationsBefore,2/$rotationsBefore"
    ) ($script:DrainObservations -join ',')
    Assert-Eq 'the replacement secret is published exactly once' 1 `
        $script:PublishObservations.Count
    Assert-Eq 'publication follows the confirmed read-back of the new secret' (
        'dummy-rotated-secret-a-90|getCredential:/v1/credentials/' +
        $successCredential + '|getCredentialsTask'
    ) $script:PublishObservations[0]
    Assert-Eq 'waits only between drain attempts and non-terminal polls' '2,7,7' (
        $script:Sleeps -join ','
    )

    # ---- scenario B: failure, one retry, no publication -----------------
    $script:Sleeps = [System.Collections.Generic.List[int]]::new()
    $script:DrainObservations = [System.Collections.Generic.List[string]]::new()
    $script:PublishObservations = [System.Collections.Generic.List[string]]::new()
    $rotationsBefore = Get-RotationSubmissionCount
    $failure = $null
    try {
        Invoke-VcfCredentialRotation `
            -Server $connection `
            -ResourceType 'NSXT_MANAGER' `
            -ResourceName $retryResource `
            -Username 'svc-rotate-b' `
            -CredentialType 'API' `
            -DrainLimit 4 `
            -DrainIntervalSeconds 3 `
            -PollLimit 6 `
            -PollIntervalSeconds 11 `
            -SleepAction { param($seconds) $script:Sleeps.Add([int] $seconds) } `
            -DrainAction {
                param($attempt)
                $script:DrainObservations.Add("$attempt/" + (Get-RotationSubmissionCount))
                0
            } `
            -PublishAction { param($secret) $script:PublishObservations.Add($secret) } > $null
    }
    catch {
        $failure = $_.Exception
    }
    Assert-True 'a failed rotation throws' ($null -ne $failure)
    Assert-Eq 'failure exception type' 'VcfCredentialRotationFailedException' (
        $(if ($null -eq $failure) { 'none' } else { $failure.GetType().Name })
    )
    if ($null -ne $failure -and
        $failure.GetType().Name -eq 'VcfCredentialRotationFailedException') {
        Assert-Eq 'failure exception task id' $retryTask $failure.TaskId
        Assert-Eq 'failure exception status' 'FAILED' $failure.TaskStatus
        Assert-Eq 'failure exception error code' 'CREDENTIAL_ROTATE_FAILED' $failure.ErrorCode
        Assert-Eq 'failure exception records the single retry' 'True' $failure.RetryAttempted
        Assert-True 'failure exception uses the task error message' (
            $failure.Message -like '*credential rotation workflow failed*'
        )
        foreach ($secret in $secrets) {
            Assert-True 'failure exception never reveals a secret' (
                $failure.Message -notlike "*$secret*"
            )
        }
    }
    Assert-Eq 'a failed rotation publishes nothing' 0 $script:PublishObservations.Count
    Assert-Eq 'an already drained gate submits immediately' "1/$rotationsBefore" (
        $script:DrainObservations -join ','
    )
    Assert-Eq 'a retried rotation waits only between non-terminal polls' '11,11' (
        $script:Sleeps -join ','
    )

    # ---- scenario C: poll exhaustion cancels the task -------------------
    $script:Sleeps = [System.Collections.Generic.List[int]]::new()
    $script:PublishObservations = [System.Collections.Generic.List[string]]::new()
    $timeout = $null
    try {
        Invoke-VcfCredentialRotation `
            -Server $connection `
            -ResourceType 'ESXI' `
            -ResourceName $cancelResource `
            -Username 'root' `
            -CredentialType 'SSH' `
            -DrainLimit 4 `
            -DrainIntervalSeconds 3 `
            -PollLimit 3 `
            -PollIntervalSeconds 9 `
            -SleepAction { param($seconds) $script:Sleeps.Add([int] $seconds) } `
            -DrainAction { param($attempt) 0 } `
            -PublishAction { param($secret) $script:PublishObservations.Add($secret) } > $null
    }
    catch {
        $timeout = $_.Exception
    }
    Assert-True 'an unfinished rotation throws' ($null -ne $timeout)
    Assert-Eq 'timeout exception type' 'VcfCredentialRotationTimeoutException' (
        $(if ($null -eq $timeout) { 'none' } else { $timeout.GetType().Name })
    )
    if ($null -ne $timeout -and
        $timeout.GetType().Name -eq 'VcfCredentialRotationTimeoutException') {
        Assert-Eq 'timeout exception task id' $cancelTask $timeout.TaskId
        Assert-Eq 'PollLimit bounds the credentials-task reads exactly' 3 $timeout.PollCount
        Assert-Eq 'the unfinished task is cancelled' 'True' $timeout.Cancelled
        Assert-Eq 'the cancel response status is preserved' 'USER_CANCELLED' `
            $timeout.CancelStatus
        foreach ($secret in $secrets) {
            Assert-True 'timeout exception never reveals a secret' (
                $timeout.Message -notlike "*$secret*"
            )
        }
    }
    Assert-Eq 'an unfinished rotation publishes nothing' 0 $script:PublishObservations.Count
    Assert-Eq 'waits only between the permitted polls' '9,9' ($script:Sleeps -join ',')

    # ---- scenario D: callers never drain, so nothing is mutated ---------
    $script:Sleeps = [System.Collections.Generic.List[int]]::new()
    $script:PublishObservations = [System.Collections.Generic.List[string]]::new()
    $stuck = $null
    try {
        Invoke-VcfCredentialRotation `
            -Server $connection `
            -ResourceType 'VCENTER' `
            -ResourceName $drainResource `
            -Username 'svc-rotate-d@vsphere.local' `
            -CredentialType 'SSO' `
            -DrainLimit 3 `
            -DrainIntervalSeconds 4 `
            -PollLimit 6 `
            -PollIntervalSeconds 5 `
            -SleepAction { param($seconds) $script:Sleeps.Add([int] $seconds) } `
            -DrainAction { param($attempt) 5 } `
            -PublishAction { param($secret) $script:PublishObservations.Add($secret) } > $null
    }
    catch {
        $stuck = $_.Exception
    }
    Assert-True 'an undrained gate throws' ($null -ne $stuck)
    Assert-Eq 'drain timeout exception type' 'VcfCredentialDrainTimeoutException' (
        $(if ($null -eq $stuck) { 'none' } else { $stuck.GetType().Name })
    )
    if ($null -ne $stuck -and
        $stuck.GetType().Name -eq 'VcfCredentialDrainTimeoutException') {
        Assert-Eq 'drain timeout resource name' $drainResource $stuck.ResourceName
        Assert-Eq 'DrainLimit bounds the drain attempts exactly' 3 $stuck.DrainAttempts
        Assert-Eq 'drain timeout reports the in-flight callers' 5 $stuck.InFlightCount
    }
    Assert-Eq 'an undrained gate publishes nothing' 0 $script:PublishObservations.Count
    Assert-Eq 'waits only between drain attempts' '4,4' ($script:Sleeps -join ',')

    # ---- recorded wire history ------------------------------------------
    $log = @(Get-RequestLog)
    $expectedSequence = @(
        'createToken', $null,
        'getCredentials', 'updateOrRotatePasswords',
        'getCredentialsTask', 'getCredentialsTask', 'getCredentialsTask',
        'getCredential',
        'getCredentials', 'updateOrRotatePasswords',
        'getCredentialsTask', 'getCredentialsTask',
        'retryCredentialsTask',
        'getCredentialsTask', 'getCredentialsTask',
        'getCredentials', 'updateOrRotatePasswords',
        'getCredentialsTask', 'getCredentialsTask', 'getCredentialsTask',
        'cancelCredentialsTask',
        'getCredentials'
    )
    Assert-Eq 'exact REST operation sequence' ($expectedSequence -join ',') (
        ($log.operationId) -join ','
    )
    Assert-Eq 'total wire request count' 22 $log.Count
    Assert-Eq 'the loopback service sees only contract operations' (
        'cancelCredentialsTask,createToken,getCredential,getCredentials,' +
        'getCredentialsTask,retryCredentialsTask,updateOrRotatePasswords'
    ) (
        (@($log.operationId | Where-Object { $null -ne $_ } | Sort-Object -Unique)) -join ','
    )
    Assert-True 'every request is answered by the contract, never rejected' (
        @($log | Where-Object { $_.responseStatus -ge 300 }).Count -eq 0
    )
    Assert-True 'every request stays on the loopback authority' (
        @($log | Where-Object { $_.headers.host -cne "127.0.0.1:$port" }).Count -eq 0
    )
    Assert-True 'every request accepts JSON' (
        @($log | Where-Object { $_.headers.accept -notlike '*application/json*' }).Count -eq 0
    )

    $probes = @(
        $log | Where-Object { $null -eq $_.operationId -and $_.path -ceq '/v1/sddc-manager' }
    )
    Assert-Eq 'one SDK connection probe outside the contract operations' 1 $probes.Count

    $tokens = @($log | Where-Object operationId -CEQ 'createToken')
    Assert-Eq 'one SDK token request' 1 $tokens.Count
    Assert-Eq 'token target' '/v1/tokens' $tokens[0].rawTarget
    Assert-Eq 'token request is not bearer authenticated' '' $tokens[0].authorization

    $lookups = @($log | Where-Object operationId -CEQ 'getCredentials')
    Assert-Eq 'one credential lookup per rotation attempt' 4 $lookups.Count
    Assert-True 'every lookup is a bodyless GET' (
        @($lookups | Where-Object { $_.method -cne 'GET' -or $_.bodyLength -ne 0 }).Count -eq 0
    )
    Assert-True 'every lookup carries the SDK bearer token' (
        @($lookups | Where-Object { $_.authorization -cne "Bearer $accessToken" }).Count -eq 0
    )
    Assert-True 'no lookup sends an empty query value' (
        @($lookups | Where-Object { $_.rawQuery -match '=(&|$)' }).Count -eq 0
    )
    Assert-Eq 'the bound lookup filters travel, and only those' `
        'accountType,resourceName,resourceType' (
        (Get-JsonMemberNames $lookups[0].query) -join ','
    )
    Assert-Eq 'lookup resource name' $successResource $lookups[0].query.resourceName
    Assert-Eq 'lookup resource type' 'VCENTER' $lookups[0].query.resourceType
    Assert-Eq 'lookup account type' 'USER' $lookups[0].query.accountType
    foreach ($index in 1, 2, 3) {
        Assert-Eq "unset optional lookup filters are omitted (request $index)" `
            'resourceName,resourceType' (
            (Get-JsonMemberNames $lookups[$index].query) -join ','
        )
    }

    $rotations = @($log | Where-Object operationId -CEQ 'updateOrRotatePasswords')
    Assert-Eq 'three rotations reach the wire' 3 $rotations.Count
    Assert-True 'every rotation is a PATCH on the collection with no query' (
        @($rotations | Where-Object {
            $_.method -cne 'PATCH' -or
            $_.path -cne '/v1/credentials' -or
            $_.rawQuery -cne ''
        }).Count -eq 0
    )
    Assert-True 'every rotation sends JSON' (
        @($rotations | Where-Object { $_.contentType -notlike 'application/json*' }).Count -eq 0
    )
    Assert-True 'every rotation is accepted' (
        @($rotations | Where-Object { $_.responseStatus -ne 202 }).Count -eq 0
    )
    Assert-True 'the undrained resource never reaches the wire' (
        @($rotations | Where-Object { $_.body -like "*$drainResource*" }).Count -eq 0
    )

    $rotateBody = $rotations[0].body | ConvertFrom-Json
    Assert-Eq 'rotation body member set' 'elements,operationType' (
        (Get-JsonMemberNames $rotateBody) -join ','
    )
    Assert-Eq 'rotation operation type' 'ROTATE' $rotateBody.operationType
    Assert-Eq 'rotation carries exactly one resource element' 1 @($rotateBody.elements).Count
    $element = @($rotateBody.elements)[0]
    Assert-Eq 'resource element member set' 'credentials,resourceName,resourceType' (
        (Get-JsonMemberNames $element) -join ','
    )
    Assert-Eq 'resource element name' $successResource $element.resourceName
    Assert-Eq 'resource element type' 'VCENTER' $element.resourceType
    Assert-Eq 'resource element carries exactly one credential' 1 @($element.credentials).Count
    $credentialElement = @($element.credentials)[0]
    Assert-Eq 'bound credential member set' 'accountType,credentialType,username' (
        (Get-JsonMemberNames $credentialElement) -join ','
    )
    Assert-Eq 'credential type' 'SSO' $credentialElement.credentialType
    Assert-Eq 'credential account type' 'USER' $credentialElement.accountType
    Assert-Eq 'credential username' 'svc-rotate-a@vsphere.local' $credentialElement.username

    foreach ($index in 1, 2) {
        $body = $rotations[$index].body | ConvertFrom-Json
        Assert-Eq "rotation $index omits the unset autoRotatePolicy" 'elements,operationType' (
            (Get-JsonMemberNames $body) -join ','
        )
        $bodyElement = @($body.elements)[0]
        Assert-Eq "rotation $index omits the unset resourceId" `
            'credentials,resourceName,resourceType' (
            (Get-JsonMemberNames $bodyElement) -join ','
        )
        Assert-Eq "rotation $index omits the unset accountType" 'credentialType,username' (
            (Get-JsonMemberNames (@($bodyElement.credentials)[0])) -join ','
        )
    }
    foreach ($rotation in $rotations) {
        Assert-True 'a ROTATE never sends a password field' (
            $rotation.body -notmatch '"password"'
        )
        foreach ($secret in $secrets) {
            Assert-True 'a rotation body never carries a secret' (
                $rotation.body -notlike "*$secret*"
            )
        }
    }

    $retries = @($log | Where-Object operationId -CEQ 'retryCredentialsTask')
    Assert-Eq 'exactly one retry' 1 $retries.Count
    Assert-Eq 'retry method' 'PATCH' $retries[0].method
    Assert-Eq 'retry target' "/v1/credentials/tasks/$retryTask" $retries[0].rawTarget
    Assert-Eq 'retry is accepted' 202 $retries[0].responseStatus
    Assert-Eq 'the retry resends the original specification unchanged' `
        $rotations[1].body $retries[0].body

    $polls = @($log | Where-Object operationId -CEQ 'getCredentialsTask')
    Assert-Eq 'total credentials-task reads' 10 $polls.Count
    Assert-True 'every task read is a bodyless GET without a query' (
        @($polls | Where-Object {
            $_.method -cne 'GET' -or $_.bodyLength -ne 0 -or $_.rawQuery -cne ''
        }).Count -eq 0
    )
    Assert-Eq 'the successful rotation is polled to a terminal state' 3 (
        @($polls | Where-Object { $_.path -ceq "/v1/credentials/tasks/$successTask" }).Count
    )
    Assert-Eq 'the retried rotation is polled on both attempts' 4 (
        @($polls | Where-Object { $_.path -ceq "/v1/credentials/tasks/$retryTask" }).Count
    )
    Assert-Eq 'the unfinished rotation is polled exactly PollLimit times' 3 (
        @($polls | Where-Object { $_.path -ceq "/v1/credentials/tasks/$cancelTask" }).Count
    )

    $cancels = @($log | Where-Object operationId -CEQ 'cancelCredentialsTask')
    Assert-Eq 'exactly one cancel' 1 $cancels.Count
    Assert-Eq 'cancel method' 'DELETE' $cancels[0].method
    Assert-Eq 'cancel target' "/v1/credentials/tasks/$cancelTask" $cancels[0].rawTarget
    Assert-Eq 'cancel is bodyless' 0 $cancels[0].bodyLength
    Assert-Eq 'cancel is accepted' 202 $cancels[0].responseStatus

    $readBacks = @($log | Where-Object operationId -CEQ 'getCredential')
    Assert-Eq 'only a confirmed rotation is read back' 1 $readBacks.Count
    Assert-Eq 'read-back target' "/v1/credentials/$successCredential" $readBacks[0].rawTarget
    Assert-Eq 'read-back is a bodyless GET' 0 $readBacks[0].bodyLength
    $readBackIndex = [array]::IndexOf($log.rawTarget, $readBacks[0].rawTarget)
    Assert-True 'the read-back follows the terminal poll of its own task' (
        $readBackIndex -gt 0 -and
        $log[$readBackIndex - 1].operationId -ceq 'getCredentialsTask' -and
        $log[$readBackIndex - 1].path -ceq "/v1/credentials/tasks/$successTask"
    )
    Assert-True 'no credential of a failed rotation is read back' (
        @(
            $readBacks | Where-Object {
                $_.path -ceq "/v1/credentials/$retryCredential" -or
                $_.path -ceq "/v1/credentials/$cancelCredential"
            }
        ).Count -eq 0
    )

    # ---- lookup rejection: zero, duplicate, and id-less matches --------
    $selectionCases = @(
        [pscustomobject]@{
            Label = 'zero matching credentials'
            ResourceName = 'vc-no-match.rainpole.io'
            Username = 'svc-no-match@vsphere.local'
        },
        [pscustomobject]@{
            Label = 'duplicate matching credentials'
            ResourceName = 'vc-duplicate.rainpole.io'
            Username = 'svc-duplicate@vsphere.local'
        },
        [pscustomobject]@{
            Label = 'matching credential without an id'
            ResourceName = 'vc-missing-id.rainpole.io'
            Username = 'svc-missing-id@vsphere.local'
        }
    )
    foreach ($selectionCase in $selectionCases) {
        $script:EdgeDrainCalls = 0
        $script:EdgePublishCalls = 0
        $before = @(Get-RequestLog).Count
        $selectionFailure = $null
        try {
            Invoke-VcfCredentialRotation `
                -Server $connection `
                -ResourceType 'VCENTER' `
                -ResourceName $selectionCase.ResourceName `
                -Username $selectionCase.Username `
                -CredentialType 'SSO' `
                -DrainLimit 1 `
                -DrainIntervalSeconds 0 `
                -PollLimit 1 `
                -PollIntervalSeconds 0 `
                -SleepAction { param($seconds) } `
                -DrainAction { $script:EdgeDrainCalls++; 0 } `
                -PublishAction { param($secret) $script:EdgePublishCalls++ } > $null
        }
        catch {
            $selectionFailure = $_.Exception
        }
        $newRecords = @(Get-RequestLog | Select-Object -Skip $before)
        Assert-True "$($selectionCase.Label) is rejected" (
            $null -ne $selectionFailure
        )
        Assert-Eq "$($selectionCase.Label) stops after the lookup" `
            'getCredentials' ($newRecords.operationId -join ',')
        Assert-Eq "$($selectionCase.Label) never starts draining" 0 `
            $script:EdgeDrainCalls
        Assert-Eq "$($selectionCase.Label) publishes nothing" 0 `
            $script:EdgePublishCalls
    }

    # ---- an accepted rotation without a task id cannot be polled -------
    $script:EdgeDrainCalls = 0
    $script:EdgePublishCalls = 0
    $script:EdgeSleeps = 0
    $before = @(Get-RequestLog).Count
    $blankTaskFailure = $null
    try {
        Invoke-VcfCredentialRotation `
            -Server $connection `
            -ResourceType 'VCENTER' `
            -ResourceName 'vc-blank-task.rainpole.io' `
            -Username 'svc-blank-task@vsphere.local' `
            -CredentialType 'SSO' `
            -DrainLimit 1 `
            -DrainIntervalSeconds 0 `
            -PollLimit 2 `
            -PollIntervalSeconds 0 `
            -SleepAction { param($seconds) $script:EdgeSleeps++ } `
            -DrainAction { $script:EdgeDrainCalls++; 0 } `
            -PublishAction { param($secret) $script:EdgePublishCalls++ } > $null
    }
    catch {
        $blankTaskFailure = $_.Exception
    }
    $newRecords = @(Get-RequestLog | Select-Object -Skip $before)
    Assert-True 'a blank accepted task id is rejected' ($null -ne $blankTaskFailure)
    Assert-Eq 'a blank task id stops before task polling' `
        'getCredentials,updateOrRotatePasswords' ($newRecords.operationId -join ',')
    Assert-Eq 'a blank task id follows one successful drain' 1 $script:EdgeDrainCalls
    Assert-Eq 'a blank task id publishes nothing' 0 $script:EdgePublishCalls
    Assert-Eq 'a blank task id waits zero times' 0 $script:EdgeSleeps

    # ---- successful tasks still require a valid credential read-back ---
    $readBackCases = @(
        [pscustomobject]@{
            Label = 'a read-back for another username'
            ResourceName = 'vc-mismatch-readback.rainpole.io'
            Username = 'svc-readback@vsphere.local'
        },
        [pscustomobject]@{
            Label = 'a read-back with an empty replacement secret'
            ResourceName = 'vc-empty-secret.rainpole.io'
            Username = 'svc-empty-secret@vsphere.local'
        }
    )
    foreach ($readBackCase in $readBackCases) {
        $script:EdgePublishCalls = 0
        $before = @(Get-RequestLog).Count
        $readBackFailure = $null
        try {
            Invoke-VcfCredentialRotation `
                -Server $connection `
                -ResourceType 'VCENTER' `
                -ResourceName $readBackCase.ResourceName `
                -Username $readBackCase.Username `
                -CredentialType 'SSO' `
                -DrainLimit 1 `
                -DrainIntervalSeconds 0 `
                -PollLimit 2 `
                -PollIntervalSeconds 0 `
                -SleepAction { param($seconds) } `
                -DrainAction { 0 } `
                -PublishAction { param($secret) $script:EdgePublishCalls++ } > $null
        }
        catch {
            $readBackFailure = $_.Exception
        }
        $newRecords = @(Get-RequestLog | Select-Object -Skip $before)
        Assert-True "$($readBackCase.Label) is rejected" ($null -ne $readBackFailure)
        Assert-Eq "$($readBackCase.Label) is checked only after task success" (
            'getCredentials,updateOrRotatePasswords,getCredentialsTask,getCredential'
        ) ($newRecords.operationId -join ',')
        Assert-Eq "$($readBackCase.Label) publishes nothing" 0 `
            $script:EdgePublishCalls
        if ($null -ne $readBackFailure) {
            foreach ($secret in $secrets) {
                Assert-True "$($readBackCase.Label) failure reveals no secret" (
                    $readBackFailure.Message -notlike "*$secret*"
                )
            }
        }
    }

    # ---- USER_CANCELLED is terminal, failed, and never retryable --------
    $script:EdgePublishCalls = 0
    $before = @(Get-RequestLog).Count
    $cancelledFailure = $null
    try {
        Invoke-VcfCredentialRotation `
            -Server $connection `
            -ResourceType 'VCENTER' `
            -ResourceName 'vc-cancelled.rainpole.io' `
            -Username 'svc-cancelled@vsphere.local' `
            -CredentialType 'SSO' `
            -DrainLimit 1 `
            -DrainIntervalSeconds 0 `
            -PollLimit 2 `
            -PollIntervalSeconds 0 `
            -SleepAction { param($seconds) } `
            -DrainAction { 0 } `
            -PublishAction { param($secret) $script:EdgePublishCalls++ } > $null
    }
    catch {
        $cancelledFailure = $_.Exception
    }
    $newRecords = @(Get-RequestLog | Select-Object -Skip $before)
    Assert-Eq 'USER_CANCELLED throws the rotation failure type' `
        'VcfCredentialRotationFailedException' (
            $(if ($null -eq $cancelledFailure) {
                'none'
            } else {
                $cancelledFailure.GetType().Name
            })
        )
    if ($null -ne $cancelledFailure -and
        $cancelledFailure.GetType().Name -eq 'VcfCredentialRotationFailedException') {
        Assert-Eq 'failed terminal status is preserved exactly' ' user cancelled ' `
            $cancelledFailure.TaskStatus
        Assert-Eq 'a failed task without errors has an empty error code' '' `
            $cancelledFailure.ErrorCode
        Assert-Eq 'USER_CANCELLED records that no retry happened' 'False' `
            $cancelledFailure.RetryAttempted
    }
    Assert-Eq 'USER_CANCELLED neither retries nor reads back nor cancels again' (
        'getCredentials,updateOrRotatePasswords,getCredentialsTask'
    ) ($newRecords.operationId -join ',')
    Assert-Eq 'USER_CANCELLED publishes nothing' 0 $script:EdgePublishCalls

    # ---- INCONSISTENT is terminal, failed, and never retryable ----------
    $script:EdgePublishCalls = 0
    $before = @(Get-RequestLog).Count
    $inconsistentFailure = $null
    try {
        Invoke-VcfCredentialRotation `
            -Server $connection `
            -ResourceType 'VCENTER' `
            -ResourceName 'vc-inconsistent.rainpole.io' `
            -Username 'svc-inconsistent@vsphere.local' `
            -CredentialType 'SSO' `
            -DrainLimit 1 `
            -DrainIntervalSeconds 0 `
            -PollLimit 2 `
            -PollIntervalSeconds 0 `
            -SleepAction { param($seconds) } `
            -DrainAction { 0 } `
            -PublishAction { param($secret) $script:EdgePublishCalls++ } > $null
    }
    catch {
        $inconsistentFailure = $_.Exception
    }
    $newRecords = @(Get-RequestLog | Select-Object -Skip $before)
    Assert-Eq 'INCONSISTENT throws the rotation failure type' `
        'VcfCredentialRotationFailedException' (
            $(if ($null -eq $inconsistentFailure) {
                'none'
            } else {
                $inconsistentFailure.GetType().Name
            })
        )
    if ($null -ne $inconsistentFailure -and
        $inconsistentFailure.GetType().Name -eq 'VcfCredentialRotationFailedException') {
        Assert-Eq 'INCONSISTENT status is preserved exactly' ' inconsistent ' `
            $inconsistentFailure.TaskStatus
        Assert-Eq 'INCONSISTENT without errors has an empty error code' '' `
            $inconsistentFailure.ErrorCode
        Assert-Eq 'INCONSISTENT records that no retry happened' 'False' `
            $inconsistentFailure.RetryAttempted
    }
    Assert-Eq 'INCONSISTENT neither retries nor reads back nor cancels' (
        'getCredentials,updateOrRotatePasswords,getCredentialsTask'
    ) ($newRecords.operationId -join ',')
    Assert-Eq 'INCONSISTENT publishes nothing' 0 $script:EdgePublishCalls

    # ---- PollLimit spans both the original task and its retry -----------
    $script:EdgeSleeps = [System.Collections.Generic.List[int]]::new()
    $script:EdgePublishCalls = 0
    $before = @(Get-RequestLog).Count
    $retryTimeout = $null
    try {
        Invoke-VcfCredentialRotation `
            -Server $connection `
            -ResourceType 'VCENTER' `
            -ResourceName 'vc-retry-timeout.rainpole.io' `
            -Username 'svc-retry-timeout@vsphere.local' `
            -CredentialType 'SSO' `
            -DrainLimit 1 `
            -DrainIntervalSeconds 0 `
            -PollLimit 3 `
            -PollIntervalSeconds 13 `
            -SleepAction { param($seconds) $script:EdgeSleeps.Add([int] $seconds) } `
            -DrainAction { 0 } `
            -PublishAction { param($secret) $script:EdgePublishCalls++ } > $null
    }
    catch {
        $retryTimeout = $_.Exception
    }
    $newRecords = @(Get-RequestLog | Select-Object -Skip $before)
    Assert-Eq 'retry-wide poll exhaustion throws the timeout type' `
        'VcfCredentialRotationTimeoutException' (
            $(if ($null -eq $retryTimeout) { 'none' } else { $retryTimeout.GetType().Name })
        )
    if ($null -ne $retryTimeout -and
        $retryTimeout.GetType().Name -eq 'VcfCredentialRotationTimeoutException') {
        Assert-Eq 'retry-wide timeout retains the original task id' `
            'ab000011-0011-4011-8011-000000000011' $retryTimeout.TaskId
        Assert-Eq 'PollLimit counts reads before and after retry together' 3 `
            $retryTimeout.PollCount
        Assert-Eq 'retry-wide timeout cancels the task' 'True' $retryTimeout.Cancelled
        Assert-Eq 'retry-wide timeout preserves cancel status' 'USER_CANCELLED' `
            $retryTimeout.CancelStatus
    }
    Assert-Eq 'retry-wide timeout performs exactly the bounded workflow' (
        'getCredentials,updateOrRotatePasswords,getCredentialsTask,' +
        'getCredentialsTask,retryCredentialsTask,getCredentialsTask,' +
        'cancelCredentialsTask'
    ) ($newRecords.operationId -join ',')
    Assert-Eq 'retry-wide timeout waits only after its one non-terminal read' `
        '13' ($script:EdgeSleeps -join ',')
    Assert-Eq 'retry-wide timeout publishes nothing' 0 $script:EdgePublishCalls
    $edgeRotation = @(
        $newRecords | Where-Object operationId -CEQ 'updateOrRotatePasswords'
    )
    $edgeRetry = @(
        $newRecords | Where-Object operationId -CEQ 'retryCredentialsTask'
    )
    Assert-Eq 'retry-wide timeout submits once' 1 $edgeRotation.Count
    Assert-Eq 'retry-wide timeout retries once' 1 $edgeRetry.Count
    if ($edgeRotation.Count -eq 1 -and $edgeRetry.Count -eq 1) {
        Assert-Eq 'retry-wide timeout resends the unchanged original body' `
            $edgeRotation[0].body $edgeRetry[0].body
    }
}
catch {
    $script:Failures++
    Write-Output "FAIL verifier setup or execution: $($_.Exception.Message)"
    if ($_.ScriptStackTrace) { Write-Output $_.ScriptStackTrace }
}
finally {
    if ($null -ne $serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
        $serverProcess.WaitForExit()
    }
    Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
    foreach ($commandName in $requiredSdkCommands) {
        Assert-True "dynamic scenarios execute genuine SDK cmdlet $commandName" (
            $global:Vcf90SdkCommandHits[$commandName] -gt 0
        )
    }
    if ($sdkBreakpoints.Count -gt 0) {
        Remove-PSBreakpoint -Breakpoint $sdkBreakpoints -ErrorAction SilentlyContinue
    }
    Remove-Variable -Name Vcf90SdkCommandHits -Scope Global `
        -ErrorAction SilentlyContinue
}

if ($script:Failures -gt 0) {
    Write-Output "FAILED: $script:Failures failure(s), $script:Checks checks"
    exit 1
}
Write-Output "ALL TESTS PASSED ($script:Checks checks)"
