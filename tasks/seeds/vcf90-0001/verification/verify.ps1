#Requires -Version 7.0
<#
    Protected verification for the VCF 9.0 bundle-download module.

    Boots the contract-pinned loopback SDDC Manager (mock_sddc.py) on 127.0.0.1,
    drives VcfBundleDownload.psm1 through the genuine VMware.Sdk.Vcf.SddcManager
    cmdlets, then reads the mock's request log and asserts the exact wire shape.

    No live VMware endpoint is contacted. Credentials are fixture dummies.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$script:Checks = 0
$script:Failures = 0
$script:SleepLog = @()

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

function Assert-MemberSet {
    param([string] $Label, [string[]] $Expected, $Object)
    $script:Checks++
    $actual = @()
    if ($null -ne $Object) {
        $actual = @($Object.PSObject.Properties.Name) | Sort-Object
    }
    $want = @($Expected) | Sort-Object
    if (($actual -join ',') -ceq ($want -join ',')) { return }
    $script:Failures++
    Write-Output "FAIL $Label"
    Write-Output "  expected members: $($want -join ', ')"
    Write-Output "  actual members:   $($actual -join ', ')"
}

function Assert-Absent {
    param([string] $Label, $Object, [string] $Name)
    $script:Checks++
    $names = @()
    if ($null -ne $Object) { $names = @($Object.PSObject.Properties.Name) }
    if ($names -cnotcontains $Name) { return }
    $script:Failures++
    Write-Output "FAIL $Label"
    Write-Output "  member '$Name' was present on the wire but was never requested"
}

function Get-RequestLog {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return @() }
    @(
        Get-Content -LiteralPath $Path |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
}

# --------------------------------------------------------------- fixture data

$ImmediateBundle = 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa'
$ScheduledBundle = 'bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb'
$TrapBundle      = 'cccccccc-3333-4333-8333-cccccccccccc'
$TimeoutBundle   = 'dddddddd-4444-4444-8444-dddddddddddd'
$MissingBundle   = 'eeeeeeee-5555-4555-8555-eeeeeeeeeeee'
$SkippedBundle   = 'ffffffff-6666-4666-8666-ffffffffffff'
$CancelledBundle = 'abababab-7777-4777-8777-abababababab'
$EmptyTaskBundle = 'cdcdcdcd-8888-4888-8888-cdcdcdcdcdcd'

$ImmediateTask = '11111111-1111-4111-8111-111111111111'
$ScheduledTask = '22222222-2222-4222-8222-222222222222'
$TrapTask      = '33333333-3333-4333-8333-333333333333'
$TimeoutTask   = '44444444-4444-4444-8444-444444444444'
$SkippedTask   = '55555555-5555-4555-8555-555555555555'
$CancelledTask = '66666666-6666-4666-8666-666666666666'

$FixtureUser     = 'svc-vcf-depot@vsphere.local'
$FixturePassword = 'dummy-vcf-login-pass-90'
$FixtureToken    = 'dummy-vcf-access-token-90'

$ScheduleStamp = '2026-03-01T09:00:00Z'

$ContractOperationIds = @('createToken', 'getBundle', 'startBundleDownloadByID', 'getTask')

# ------------------------------------------------------------ candidate check

$modulePath = Join-Path $PSScriptRoot 'VcfBundleDownload.psm1'
Assert-True 'VcfBundleDownload.psm1 exists at the workspace root' (
    Test-Path -LiteralPath $modulePath -PathType Leaf
)
if ($script:Failures -gt 0) {
    Write-Output "FAILED: $script:Failures failure(s), $script:Checks checks"
    exit 1
}

# The wire assertions below prove that the real SDK reaches the loopback
# service. These AST checks also ensure the candidate actually expresses every
# required SDK operation, rather than recreating the same HTTP exchange with a
# different transport. Inspecting syntax nodes avoids rejecting harmless words
# in comments or error messages.
$source = Get-Content -LiteralPath $modulePath -Raw
$parseTokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $source,
    [ref] $parseTokens,
    [ref] $parseErrors
)
Assert-Eq 'module source parses without errors' 0 @($parseErrors).Count

$allCommandAsts = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
}, $true))
$forbiddenCommandUses = @($allCommandAsts | Where-Object {
    $name = $_.GetCommandName()
    $name -match '(^|[\\/])(curl|wget)(\.exe)?$' -or
        $name -imatch '^(Invoke-WebRequest|Invoke-RestMethod|iwr|irm)$'
})
Assert-Eq 'module does not invoke a forbidden HTTP command' 0 $forbiddenCommandUses.Count

$usedTypeNames = @(
    $ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.TypeExpressionAst]
    }, $true) |
        ForEach-Object { $_.TypeName.FullName }
)
$forbiddenTypeUses = @($usedTypeNames | Where-Object {
    $_ -imatch '^System\.Net\.(Http|Sockets)(\.|$)' -or
        $_ -imatch '(^|\.)(HttpClient|HttpListener|HttpWebRequest|WebClient|WebRequest|TcpClient|Socket|RestClient)$'
})
Assert-Eq 'module does not instantiate a forbidden HTTP or socket type' 0 `
    $forbiddenTypeUses.Count

$forbiddenNewObjectUses = @($allCommandAsts | Where-Object {
    $_.GetCommandName() -ieq 'New-Object' -and
        $_.Extent.Text -imatch '(System\.Net\.(Http|Sockets)|HttpClient|HttpListener|HttpWebRequest|WebClient|WebRequest|TcpClient|RestClient)'
})
Assert-Eq 'module does not construct a forbidden transport through New-Object' 0 `
    $forbiddenNewObjectUses.Count

$functionAst = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -ceq 'Start-VcfBundleDownloadAndWait'
}, $true)
Assert-True 'module defines Start-VcfBundleDownloadAndWait as a function' ($null -ne $functionAst)

if ($null -ne $functionAst) {
    foreach ($default in @(
        @{ Name = 'PollLimit'; Value = '120' },
        @{ Name = 'PollIntervalSeconds'; Value = '5' }
    )) {
        $parameterAst = $functionAst.Body.ParamBlock.Parameters |
            Where-Object { $_.Name.VariablePath.UserPath -ceq $default.Name } |
            Select-Object -First 1
        $defaultText = if ($null -eq $parameterAst -or $null -eq $parameterAst.DefaultValue) {
            ''
        }
        else {
            $parameterAst.DefaultValue.Extent.Text.Trim()
        }
        Assert-Eq "$($default.Name) has the required default" $default.Value $defaultText
    }
}

# ------------------------------------------------------------- contract guard

$contractPath = Join-Path $PSScriptRoot 'docs/contract.json'
$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
Assert-Eq 'contract is pinned to the 9.0.0.0 specification revision' '9.0.0.0' $contract.source.apiVersion
Assert-Eq 'contract is pinned to the tagged commit' '85151f6b1bb58f13b6ac0304bfec53904bea085f' $contract.source.commitSha
Assert-Eq 'contract comes from the specification, not a documentation page' 'openapi-specification' $contract.source.sourceKind
Assert-Eq 'contract names exactly the operations in scope' `
    (($ContractOperationIds | Sort-Object) -join ',') `
    ((@($contract.operations.operationId) | Sort-Object) -join ',')

# ------------------------------------------------------------------ mock boot

$workDir = Join-Path $PSScriptRoot '_verification'
if (Test-Path -LiteralPath $workDir) {
    Remove-Item -LiteralPath $workDir -Recurse -Force
}
New-Item -ItemType Directory -Path $workDir | Out-Null

$portFile   = Join-Path $workDir 'port.txt'
$requestLog = Join-Path $workDir 'requests.jsonl'
$serverOut  = Join-Path $workDir 'mock.out'
$serverErr  = Join-Path $workDir 'mock.err'

$serverProcess = $null
$sdkBreakpoints = @()
try {
    $serverProcess = Start-Process -FilePath 'python3' `
        -ArgumentList @((Join-Path $PSScriptRoot 'mock_sddc.py'), $portFile, $requestLog) `
        -PassThru -NoNewWindow `
        -RedirectStandardOutput $serverOut -RedirectStandardError $serverErr

    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Path -LiteralPath $portFile -PathType Leaf)) {
        if ($serverProcess.HasExited -or [DateTime]::UtcNow -gt $deadline) {
            $detail = Get-Content -LiteralPath $serverErr -Raw -ErrorAction SilentlyContinue
            throw "loopback mock failed to start: $detail"
        }
        Start-Sleep -Milliseconds 40
    }
    $port = [int] (Get-Content -LiteralPath $portFile -Raw).Trim()

    Import-Module -Name $modulePath -Force -ErrorAction Stop

    # The candidate must load its genuine SDK dependency itself. Check that
    # before importing the SDK into the verifier's global session state for the
    # connection setup below.
    $candidateSdkModules = @(Get-Module -Name 'VMware.Sdk.Vcf.SddcManager' -All)
    Assert-True 'candidate imports VMware.Sdk.Vcf.SddcManager' `
        ($candidateSdkModules.Count -gt 0)
    if ($candidateSdkModules.Count -gt 0) {
        $candidateSdkVersion = $candidateSdkModules |
            Measure-Object -Property Version -Maximum |
            Select-Object -ExpandProperty Maximum
        Assert-True 'candidate imports the required VMware SDK version' (
            $candidateSdkVersion -ge [version] '13.5.0.25380678'
        )
    }

    Import-Module -Name 'VMware.Sdk.Vcf.SddcManager' -MinimumVersion '13.5.0.25380678' `
        -ErrorAction Stop -WarningAction SilentlyContinue

    $exported = (Get-Module -Name 'VcfBundleDownload').ExportedFunctions.Keys
    Assert-Eq 'module exports only Start-VcfBundleDownloadAndWait' `
        'Start-VcfBundleDownloadAndWait' (($exported | Sort-Object) -join ',')

    $server = Connect-VcfSddcManagerServer -Server '127.0.0.1' -Port $port -Protocol 'http' `
        -User $FixtureUser -Password $FixturePassword `
        -NotDefault -IgnoreInvalidCertificate -WarningAction SilentlyContinue

    $sleepAction = { param($seconds) $script:SleepLog += $seconds }

    # Command breakpoints observe the genuine SDK invocations without mocking,
    # wrapping or replacing them. Wire assertions later independently verify
    # what those commands actually sent to the loopback service.
    $script:SdkCallCounts = @{
        GetBundle          = 0
        InitializeDownload = 0
        InitializeUpdate   = 0
        StartDownload      = 0
        GetTask            = 0
    }
    $sdkBreakpoints = @(
        Set-PSBreakpoint -Command 'Invoke-VcfGetBundle' -Action {
            $script:SdkCallCounts.GetBundle++
        }
        Set-PSBreakpoint -Command 'Initialize-VcfBundleDownloadSpec' -Action {
            $script:SdkCallCounts.InitializeDownload++
        }
        Set-PSBreakpoint -Command 'Initialize-VcfBundleUpdateSpec' -Action {
            $script:SdkCallCounts.InitializeUpdate++
        }
        Set-PSBreakpoint -Command 'Invoke-VcfStartBundleDownloadByID' -Action {
            $script:SdkCallCounts.StartDownload++
        }
        Set-PSBreakpoint -Command 'Invoke-VcfGetTask' -Action {
            $script:SdkCallCounts.GetTask++
        }
    )

    # ---------------------------------------------------- 1. immediate download

    $script:SleepLog = @()
    $immediate = Start-VcfBundleDownloadAndWait -Server $server -BundleId $ImmediateBundle `
        -PollIntervalSeconds 0 -PollLimit 10 -SleepAction $sleepAction

    Assert-MemberSet 'immediate result shape' `
        @('BundleId', 'BundleVersion', 'TaskId', 'Status', 'PollCount') $immediate
    Assert-Eq 'immediate result property order' `
        'BundleId,BundleVersion,TaskId,Status,PollCount' `
        (@($immediate.PSObject.Properties.Name) -join ',')
    Assert-Eq 'immediate BundleId' $ImmediateBundle $immediate.BundleId
    Assert-Eq 'immediate BundleVersion comes from getBundle' '9.0.0.0-24001234' $immediate.BundleVersion
    Assert-Eq 'immediate TaskId' $ImmediateTask $immediate.TaskId
    Assert-Eq 'immediate terminal status is normalized' 'SUCCESSFUL' $immediate.Status
    Assert-Eq 'immediate poll count' 3 $immediate.PollCount
    Assert-Eq 'immediate waits only between non-terminal polls' 2 $script:SleepLog.Count

    # ---------------------------------------------------- 2. scheduled download

    $script:SleepLog = @()
    $scheduled = Start-VcfBundleDownloadAndWait -Server $server -BundleId $ScheduledBundle `
        -ScheduledTimestamp $ScheduleStamp `
        -PollIntervalSeconds 7 -PollLimit 10 -SleepAction $sleepAction

    Assert-Eq 'scheduled TaskId' $ScheduledTask $scheduled.TaskId
    Assert-Eq 'scheduled BundleVersion comes from getBundle' '9.0.0.0-24005678' `
        $scheduled.BundleVersion
    Assert-Eq 'scheduled terminal status is normalized' 'COMPLETED_WITH_WARNING' $scheduled.Status
    Assert-Eq 'scheduled poll count' 2 $scheduled.PollCount
    Assert-Eq 'scheduled waits only between non-terminal polls' 1 $script:SleepLog.Count
    if ($script:SleepLog.Count -eq 1) {
        Assert-Eq 'SleepAction receives PollIntervalSeconds' 7 $script:SleepLog[0]
    }

    # ------------------------------ 3. terminal-looking 202 must still be polled

    $script:SleepLog = @()
    $failed = $null
    try {
        Start-VcfBundleDownloadAndWait -Server $server -BundleId $TrapBundle `
            -PollIntervalSeconds 0 -PollLimit 10 -SleepAction $sleepAction | Out-Null
        Assert-True 'a failing download throws instead of returning' $false
    }
    catch {
        $failed = $_.Exception
    }

    if ($null -ne $failed) {
        Assert-Eq 'failure exception type' 'VcfBundleDownloadFailedException' $failed.GetType().Name
        Assert-Eq 'failure TaskId' $TrapTask $failed.TaskId
        Assert-Eq 'failure TaskStatus is normalized' 'FAILED' $failed.TaskStatus
        Assert-Eq 'failure ErrorCode from the task error' 'BUNDLE_DOWNLOAD_FAILED' $failed.ErrorCode
        Assert-Eq 'failure ReferenceToken from the task error' 'GH7YT2' $failed.ReferenceToken
        Assert-Eq 'failure BundleId' $TrapBundle $failed.BundleId
        Assert-Eq 'failure message comes from the first task error' `
            'The depot rejected the bundle transfer in the loopback fixture.' $failed.Message
        Assert-True 'failure message does not leak the login password' (
            $failed.Message -notmatch [regex]::Escape($FixturePassword)
        )
        Assert-True 'failure message does not leak the bearer token' (
            $failed.Message -notmatch [regex]::Escape($FixtureToken)
        )
    }

    # --------------------------------------------------------------- 4. timeout

    $script:SleepLog = @()
    $timedOut = $null
    try {
        Start-VcfBundleDownloadAndWait -Server $server -BundleId $TimeoutBundle `
            -PollIntervalSeconds 0 -PollLimit 4 -SleepAction $sleepAction | Out-Null
        Assert-True 'an unfinished download throws instead of returning' $false
    }
    catch {
        $timedOut = $_.Exception
    }

    if ($null -ne $timedOut) {
        Assert-Eq 'timeout exception type' 'VcfBundleDownloadTimeoutException' $timedOut.GetType().Name
        Assert-Eq 'timeout BundleId' $TimeoutBundle $timedOut.BundleId
        Assert-Eq 'timeout TaskId' $TimeoutTask $timedOut.TaskId
        Assert-Eq 'timeout PollCount equals PollLimit' 4 $timedOut.PollCount
        Assert-Eq 'timeout does not wait after the final permitted poll' 3 $script:SleepLog.Count
        Assert-True 'timeout message does not leak the login password' (
            $timedOut.Message -notmatch [regex]::Escape($FixturePassword)
        )
        Assert-True 'timeout message does not leak the bearer token' (
            $timedOut.Message -notmatch [regex]::Escape($FixtureToken)
        )
    }

    # ----------------------------- 5. remaining successful terminal: SKIPPED

    # Omitting SleepAction exercises the Start-Sleep branch. A command
    # breakpoint observes (but does not replace) the genuine invocation. The
    # zero interval keeps verification fast and deterministic.
    $script:StartSleepCalls = 0
    $startSleepBreakpoint = Set-PSBreakpoint -Command 'Start-Sleep' -Action {
        $script:StartSleepCalls++
    }
    try {
        $skipped = Start-VcfBundleDownloadAndWait -Server $server -BundleId $SkippedBundle `
            -PollIntervalSeconds 0 -PollLimit 4
    }
    finally {
        Remove-PSBreakpoint -Breakpoint $startSleepBreakpoint
    }

    Assert-Eq 'skipped TaskId' $SkippedTask $skipped.TaskId
    Assert-Eq 'skipped BundleVersion comes from getBundle' '9.0.0.0-24007890' `
        $skipped.BundleVersion
    Assert-Eq 'skipped status is trimmed and normalized' 'SKIPPED' $skipped.Status
    Assert-Eq 'skipped poll count' 2 $skipped.PollCount
    Assert-Eq 'skipped waits once with genuine Start-Sleep' 1 $script:StartSleepCalls

    # ---------------------------- 6. remaining failed terminal: CANCELLED

    $cancelled = $null
    try {
        Start-VcfBundleDownloadAndWait -Server $server -BundleId $CancelledBundle `
            -PollIntervalSeconds 0 -PollLimit 4 -SleepAction $sleepAction | Out-Null
        Assert-True 'a cancelled download throws instead of returning' $false
    }
    catch {
        $cancelled = $_.Exception
    }

    if ($null -ne $cancelled) {
        Assert-Eq 'cancelled exception type' 'VcfBundleDownloadFailedException' `
            $cancelled.GetType().Name
        Assert-Eq 'cancelled TaskId' $CancelledTask $cancelled.TaskId
        Assert-Eq 'cancelled status is trimmed and normalized' 'CANCELLED' $cancelled.TaskStatus
        Assert-Eq 'cancelled BundleId' $CancelledBundle $cancelled.BundleId
        Assert-Eq 'cancelled ErrorCode is empty without a task error' '' $cancelled.ErrorCode
        Assert-Eq 'cancelled ReferenceToken is empty without a task error' '' `
            $cancelled.ReferenceToken
        Assert-True 'cancelled message does not leak the login password' (
            $cancelled.Message -notmatch [regex]::Escape($FixturePassword)
        )
        Assert-True 'cancelled message does not leak the bearer token' (
            $cancelled.Message -notmatch [regex]::Escape($FixtureToken)
        )
    }

    # --------------------------------- 7. submission must return a usable id

    $emptyTaskError = $null
    try {
        Start-VcfBundleDownloadAndWait -Server $server -BundleId $EmptyTaskBundle `
            -PollIntervalSeconds 0 -PollLimit 3 -SleepAction $sleepAction | Out-Null
    }
    catch {
        $emptyTaskError = $_.Exception
    }
    Assert-True 'a whitespace-only task id is rejected before polling' ($null -ne $emptyTaskError)
    if ($null -ne $emptyTaskError) {
        Assert-True 'empty-task-id message does not leak the login password' (
            $emptyTaskError.Message -notmatch [regex]::Escape($FixturePassword)
        )
        Assert-True 'empty-task-id message does not leak the bearer token' (
            $emptyTaskError.Message -notmatch [regex]::Escape($FixtureToken)
        )
    }

    # -------------------------------- 8. unknown bundle fails before submitting

    $missingThrew = $false
    try {
        Start-VcfBundleDownloadAndWait -Server $server -BundleId $MissingBundle `
            -PollIntervalSeconds 0 -PollLimit 3 -SleepAction $sleepAction | Out-Null
    }
    catch {
        $missingThrew = $true
    }
    Assert-True 'an unknown bundle id fails' $missingThrew

    # ------------------------------------------------------ 9. argument guards

    foreach ($case in @(
        @{ Label = 'empty BundleId'; Args = @{ BundleId = '   ' } },
        @{ Label = 'PollLimit below 1'; Args = @{ BundleId = $ImmediateBundle; PollLimit = 0 } },
        @{ Label = 'negative PollIntervalSeconds'; Args = @{ BundleId = $ImmediateBundle; PollIntervalSeconds = -1 } }
    )) {
        $guarded = $false
        try {
            $arguments = $case.Args
            Start-VcfBundleDownloadAndWait -Server $server @arguments | Out-Null
        }
        catch {
            $guarded = $true
        }
        Assert-True "rejects $($case.Label)" $guarded
    }

    # ------------------------------------------------------------ wire assertions

    Assert-Eq 'candidate invokes genuine Invoke-VcfGetBundle calls' 8 `
        $script:SdkCallCounts.GetBundle
    Assert-Eq 'candidate invokes genuine Initialize-VcfBundleDownloadSpec calls' 7 `
        $script:SdkCallCounts.InitializeDownload
    Assert-Eq 'candidate invokes genuine Initialize-VcfBundleUpdateSpec calls' 7 `
        $script:SdkCallCounts.InitializeUpdate
    Assert-Eq 'candidate invokes genuine Invoke-VcfStartBundleDownloadByID calls' 7 `
        $script:SdkCallCounts.StartDownload
    Assert-Eq 'candidate invokes genuine Invoke-VcfGetTask calls' 14 `
        $script:SdkCallCounts.GetTask

    $log = Get-RequestLog -Path $requestLog
    Assert-True 'the mock recorded requests' ($log.Count -gt 0)

    foreach ($entry in $log) {
        Assert-True "request $($entry.sequence) targets a contract operation or the SDK connection probe" (
            $entry.connectionProbe -or ($ContractOperationIds -ccontains $entry.operationId)
        )
        Assert-Eq "request $($entry.sequence) carries no query string" '' $entry.query
    }

    # createToken: the SDK sends only the members it was given.
    $tokenCalls = @($log | Where-Object { $_.operationId -ceq 'createToken' })
    Assert-Eq 'createToken is called exactly once' 1 $tokenCalls.Count
    if ($tokenCalls.Count -eq 1) {
        $tokenBody = $tokenCalls[0].json
        Assert-Eq 'createToken uses POST' 'POST' $tokenCalls[0].method
        Assert-Eq 'createToken targets /v1/tokens' '/v1/tokens' $tokenCalls[0].path
        Assert-MemberSet 'createToken body members' @('username', 'password') $tokenBody
        Assert-Eq 'createToken username' $FixtureUser $tokenBody.username
        Assert-Absent 'createToken omits unset optional apiKey' $tokenBody 'apiKey'
        Assert-Absent 'createToken omits unset optional idToken' $tokenBody 'idToken'
    }

    # Every operation after authentication is bearer-authorized.
    foreach ($entry in @($log | Where-Object { $_.operationId -cne 'createToken' })) {
        Assert-Eq "request $($entry.sequence) is bearer authorized" "Bearer $FixtureToken" $entry.authorization
    }

    # getBundle pre-flight, including the one that 404s before any submission.
    $bundleReads = @($log | Where-Object { $_.operationId -ceq 'getBundle' })
    Assert-Eq 'getBundle is called once per download attempt' 8 $bundleReads.Count
    foreach ($entry in $bundleReads) {
        Assert-Eq "getBundle $($entry.sequence) uses GET" 'GET' $entry.method
        Assert-Eq "getBundle $($entry.sequence) sends no body" '' $entry.body
    }

    $submissions = @($log | Where-Object { $_.operationId -ceq 'startBundleDownloadByID' })
    Assert-Eq 'a download is submitted only for bundles that exist' 7 $submissions.Count
    Assert-True 'no download is submitted for the unknown bundle' (
        @($submissions | Where-Object { $_.pathParameters.id -ceq $MissingBundle }).Count -eq 0
    )

    foreach ($entry in $submissions) {
        Assert-Eq "submission $($entry.sequence) uses PATCH" 'PATCH' $entry.method
        Assert-Eq "submission $($entry.sequence) targets /v1/bundles/{id}" `
            "/v1/bundles/$($entry.pathParameters.id)" $entry.path
        Assert-True "submission $($entry.sequence) is sent as JSON" (
            "$($entry.contentType)" -match '^application/json'
        )
        Assert-MemberSet "submission $($entry.sequence) BundleUpdateSpec members" `
            @('bundleDownloadSpec') $entry.json
    }

    # The immediate submissions send downloadNow and nothing else. The raw body
    # is compared verbatim: each spec carries exactly one member, so there is no
    # ordering ambiguity, and an unset member cannot hide as null or "".
    $expectedImmediateBody = '{"bundleDownloadSpec":{"downloadNow":true}}'
    foreach ($bundleId in @(
        $ImmediateBundle,
        $TrapBundle,
        $TimeoutBundle,
        $SkippedBundle,
        $CancelledBundle,
        $EmptyTaskBundle
    )) {
        $entry = @($submissions | Where-Object { $_.pathParameters.id -ceq $bundleId }) | Select-Object -First 1
        Assert-True "an immediate download was submitted for $bundleId" ($null -ne $entry)
        if ($null -ne $entry) {
            $spec = $entry.json.bundleDownloadSpec
            Assert-MemberSet "immediate BundleDownloadSpec members for $bundleId" @('downloadNow') $spec
            Assert-Eq "immediate downloadNow for $bundleId" 'True' $spec.downloadNow
            Assert-Absent "immediate omits unset scheduledTimestamp for $bundleId" $spec 'scheduledTimestamp'
            Assert-Absent "immediate omits unset cancelNow for $bundleId" $spec 'cancelNow'
            Assert-Eq "immediate raw request body for $bundleId" $expectedImmediateBody $entry.body
        }
    }

    # The scheduled submission sends scheduledTimestamp and nothing else.
    $expectedScheduledBody = '{"bundleDownloadSpec":{"scheduledTimestamp":"' + $ScheduleStamp + '"}}'
    $scheduledEntry = @($submissions | Where-Object { $_.pathParameters.id -ceq $ScheduledBundle }) |
        Select-Object -First 1
    Assert-True 'a scheduled download was submitted' ($null -ne $scheduledEntry)
    if ($null -ne $scheduledEntry) {
        $spec = $scheduledEntry.json.bundleDownloadSpec
        Assert-MemberSet 'scheduled BundleDownloadSpec members' @('scheduledTimestamp') $spec
        Assert-Absent 'scheduled omits unset downloadNow' $spec 'downloadNow'
        Assert-Absent 'scheduled omits unset cancelNow' $spec 'cancelNow'
        Assert-Eq 'scheduled raw request body' $expectedScheduledBody $scheduledEntry.body
    }

    # Polling: the submission response is never treated as the terminal answer.
    $polls = @($log | Where-Object { $_.operationId -ceq 'getTask' })
    Assert-Eq 'total getTask polls' 14 $polls.Count
    foreach ($entry in $polls) {
        Assert-Eq "poll $($entry.sequence) uses GET" 'GET' $entry.method
        Assert-Eq "poll $($entry.sequence) sends no body" '' $entry.body
    }
    Assert-Eq 'the terminal-looking 202 is still polled' 2 (
        @($polls | Where-Object { $_.pathParameters.id -ceq $TrapTask }).Count
    )

    # Per-scenario ordering: read the bundle, submit, then poll that task id.
    foreach ($pair in @(
        @{ Bundle = $ImmediateBundle; Task = $ImmediateTask; Polls = 3 },
        @{ Bundle = $ScheduledBundle; Task = $ScheduledTask; Polls = 2 },
        @{ Bundle = $TrapBundle;      Task = $TrapTask;      Polls = 2 },
        @{ Bundle = $TimeoutBundle;   Task = $TimeoutTask;   Polls = 4 },
        @{ Bundle = $SkippedBundle;   Task = $SkippedTask;   Polls = 2 },
        @{ Bundle = $CancelledBundle; Task = $CancelledTask; Polls = 1 },
        @{ Bundle = $EmptyTaskBundle; Task = '   ';          Polls = 0 }
    )) {
        $read = @($log | Where-Object {
            $_.operationId -ceq 'getBundle' -and $_.pathParameters.id -ceq $pair.Bundle
        }) | Select-Object -First 1
        $submit = @($log | Where-Object {
            $_.operationId -ceq 'startBundleDownloadByID' -and $_.pathParameters.id -ceq $pair.Bundle
        }) | Select-Object -First 1
        $taskPolls = @($log | Where-Object {
            $_.operationId -ceq 'getTask' -and $_.pathParameters.id -ceq $pair.Task
        })

        Assert-Eq "poll count for $($pair.Task)" $pair.Polls $taskPolls.Count
        if ($null -ne $read -and $null -ne $submit) {
            Assert-True "$($pair.Bundle) is read before it is submitted" (
                $read.sequence -lt $submit.sequence
            )
        }
        if ($null -ne $submit -and $taskPolls.Count -gt 0) {
            Assert-True "$($pair.Task) is polled only after submission" (
                ($taskPolls | Measure-Object -Property sequence -Minimum).Minimum -gt $submit.sequence
            )
        }
    }
}
finally {
    if ($sdkBreakpoints.Count -gt 0) {
        Remove-PSBreakpoint -Breakpoint $sdkBreakpoints -ErrorAction SilentlyContinue
    }
    if ($null -ne $serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
        $serverProcess.WaitForExit()
    }
}

if ($script:Failures -gt 0) {
    Write-Output "FAILED: $script:Failures failure(s), $script:Checks checks"
    exit 1
}
Write-Output "ALL TESTS PASSED ($script:Checks checks)"
