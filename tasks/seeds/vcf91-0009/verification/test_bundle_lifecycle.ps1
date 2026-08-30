$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest

$script:Passed = 0

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw "ASSERTION FAILED: $Message"
    }
    $script:Passed++
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Message)
    if ([string]$Expected -cne [string]$Actual) {
        throw "ASSERTION FAILED: $Message`nexpected: <$Expected>`nactual:   <$Actual>"
    }
    $script:Passed++
}

$root = $PSScriptRoot
$modulePath = Join-Path $root 'VcfBundleLifecycle.psm1'
$contractPath = Join-Path $root 'docs/contract.json'
$sourcesPath = Join-Path $root 'docs/official_sources.json'
$mockPath = Join-Path $root 'mock_sddc.py'
$portFile = Join-Path $root '.mock-sddc-port'
$logFile = Join-Path $root '.mock-sddc-requests.jsonl'

Assert-True (Test-Path -LiteralPath $modulePath) 'VcfBundleLifecycle.psm1 must exist at repository root'

$sources = Get-Content -LiteralPath $sourcesPath -Raw | ConvertFrom-Json
$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
$expectedSha = '3949fc33339fc5ea1b77eadb258f1cf49aa88e26'
$expectedSpec = 'specifications/sddc-manager/sddc-manager-openapi.json'
$expectedOps = @(
    'createToken',
    'getApplianceInfo',
    'getBundles',
    'startBundleDownloadByID',
    'getTask'
)
Assert-Equal $expectedSha $sources.repository_commit_sha 'official source commit must stay pinned'
Assert-Equal $expectedSpec $sources.spec_path 'official source spec path must stay pinned'
Assert-Equal 'Apache-2.0' $sources.license 'official source must retain the repository license'
Assert-Equal '9.1.0.0' $sources.spec_version 'official source must identify VCF 9.1'
Assert-Equal $expectedSha $contract.derived_from.commit 'contract must record its source commit'
Assert-Equal $expectedSpec $contract.derived_from.path 'contract must record its source path'
Assert-Equal ($expectedOps -join ',') (@($sources.operations.operationId) -join ',') 'official sources must name every exact operationId'
Assert-Equal ($expectedOps -join ',') (@($contract.operations.operationId) -join ',') 'contract operationIds must match provenance'
foreach ($sourceOperation in $sources.operations) {
    Assert-Equal $expectedSha $sourceOperation.repository_commit_sha "operation $($sourceOperation.operationId) must record the commit"
    Assert-Equal $expectedSpec $sourceOperation.spec_path "operation $($sourceOperation.operationId) must record the spec path"
}

$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $modulePath,
    [ref]$tokens,
    [ref]$parseErrors
)
Assert-Equal 0 @($parseErrors).Count 'module must parse without PowerShell syntax errors'
$commandNames = @(
    $ast.FindAll(
        { param($node) $node -is [System.Management.Automation.Language.CommandAst] },
        $true
    ) | ForEach-Object { $_.GetCommandName() }
)
foreach ($requiredCommand in @(
    'Import-Module',
    'Connect-VcfSddcManagerServer',
    'Invoke-VcfGetBundles',
    'Initialize-VcfBundleDownloadSpec',
    'Initialize-VcfBundleUpdateSpec',
    'Invoke-VcfStartBundleDownloadByID',
    'Invoke-VcfGetTask'
)) {
    Assert-True ($commandNames -ccontains $requiredCommand) "module must use SDK command $requiredCommand"
}
foreach ($forbiddenCommand in @(
    'Invoke-WebRequest',
    'Invoke-RestMethod',
    'curl',
    'wget'
)) {
    Assert-True (-not ($commandNames -icontains $forbiddenCommand)) "raw transport $forbiddenCommand is forbidden"
}
$moduleText = Get-Content -LiteralPath $modulePath -Raw
Assert-True ($moduleText -notmatch 'System\.Net\.Http') 'System.Net.Http bypass is forbidden'
Assert-True (
    -not (Test-Path -LiteralPath (Join-Path $root 'VMware.Sdk.Vcf.SddcManager'))
) 'the VMware SDK prerequisite must not be vendored'

$sdk = Get-Module -ListAvailable -Name VMware.Sdk.Vcf.SddcManager |
    Where-Object { $_.Version -ge [version]'13.5.0' } |
    Sort-Object Version -Descending |
    Select-Object -First 1
Assert-True ($null -ne $sdk) 'environment prerequisite VMware.Sdk.Vcf.SddcManager 13.5.0+ is missing'

Import-Module -Name $modulePath -Force
$exported = @(
    (Get-Module VcfBundleLifecycle).ExportedFunctions.Keys |
        Sort-Object
)
Assert-Equal (
    'Connect-VcfBundleManager,Get-VcfBundleCatalog,Start-VcfBundleDownload'
) ($exported -join ',') 'module must export exactly the requested public functions'

Remove-Item -LiteralPath $portFile, $logFile -Force -ErrorAction SilentlyContinue
$mock = $null
try {
    $arguments = @(
        '-B',
        $mockPath,
        '--contract',
        $contractPath,
        '--log',
        $logFile,
        '--port-file',
        $portFile
    )
    $mock = Start-Process -FilePath 'python3' -ArgumentList $arguments -PassThru -NoNewWindow
    for ($attempt = 0; $attempt -lt 100 -and -not (Test-Path -LiteralPath $portFile); $attempt++) {
        Start-Sleep -Milliseconds 25
    }
    Assert-True (Test-Path -LiteralPath $portFile) 'loopback mock did not publish its port'
    Assert-True (-not $mock.HasExited) 'loopback mock exited before verification'
    $port = [int](Get-Content -LiteralPath $portFile -Raw)

    $securePassword = ConvertTo-SecureString 'dummy-password' -AsPlainText -Force
    $credential = [pscredential]::new('dummy-user', $securePassword)
    $connection = Connect-VcfBundleManager `
        -Server '127.0.0.1' `
        -Port $port `
        -Protocol http `
        -Credential $credential
    Assert-True ($null -ne $connection) 'connection wrapper must return the SDK connection'

    $catalogFirst = @(Get-VcfBundleCatalog -Connection $connection)
    $catalogSecond = @(Get-VcfBundleCatalog -Connection $connection)
    Assert-Equal 3 $catalogFirst.Count 'first catalog call must return every bundle'
    Assert-Equal 3 $catalogSecond.Count 'second catalog call must return every bundle'
    Assert-Equal 'Bundle-case,bundle-alpha,bundle-zulu' ($catalogFirst.Id -join ',') 'first catalog response must be sorted ordinal and case-sensitive by Id'
    Assert-Equal 'Bundle-case,bundle-alpha,bundle-zulu' ($catalogSecond.Id -join ',') 'flipped catalog response must still be sorted ordinal and case-sensitive by Id'
    Assert-Equal 'Id,Version,Type,DownloadStatus' (
        @($catalogFirst[0].PSObject.Properties.Name) -join ','
    ) 'catalog object property order must be stable'

    $delays = [System.Collections.Generic.List[int]]::new()
    $final = Start-VcfBundleDownload `
        -Connection $connection `
        -BundleId 'bundle-alpha' `
        -PollIntervalSeconds 7 `
        -PollLimit 4 `
        -SleepAction { param($seconds) $delays.Add([int]$seconds) }
    Assert-Equal 'task-download-001' $final.Id 'final task id must come from accepted task'
    Assert-Equal 'Successful' $final.Status 'function must return the terminal SDK task'
    Assert-Equal '7' ($delays -join ',') 'injected SleepAction must be used exactly between non-terminal polls'

    $caught = $null
    try {
        Start-VcfBundleDownload `
            -Connection $connection `
            -BundleId 'bundle-zulu' `
            -PollIntervalSeconds 0 `
            -PollLimit 2 `
            -SleepAction { param($seconds) } | Out-Null
    } catch {
        $caught = $_.Exception
    }
    Assert-True ($null -ne $caught) 'failed terminal task must throw'
    Assert-Equal 'VcfBundleTaskException' $caught.GetType().Name 'failed task must use the typed exception'
    Assert-Equal 'task-download-fail' $caught.TaskId 'typed failure must retain TaskId'
    Assert-Equal 'FAILED' $caught.TaskStatus 'typed failure must retain normalized status'
    Assert-True ($null -ne $caught.Task) 'typed failure must retain the final task object'

    $timeout = $null
    try {
        Start-VcfBundleDownload `
            -Connection $connection `
            -BundleId 'bundle-stall' `
            -PollIntervalSeconds 0 `
            -PollLimit 2 `
            -SleepAction { param($seconds) } | Out-Null
    } catch {
        $timeout = $_.Exception
    }
    Assert-True ($null -ne $timeout) 'poll exhaustion must throw'
    Assert-Equal 'VcfBundlePollTimeoutException' $timeout.GetType().Name 'poll exhaustion must use a typed timeout'
    Assert-Equal 'task-download-stall' $timeout.TaskId 'typed timeout must retain TaskId'
    Assert-Equal 'QUEUED' $timeout.TaskStatus 'typed timeout must retain normalized last status'
    Assert-True ($null -ne $timeout.Task) 'typed timeout must retain the last task object'

    $protocol = $null
    try {
        Start-VcfBundleDownload `
            -Connection $connection `
            -BundleId 'bundle-weird' `
            -PollIntervalSeconds 0 `
            -PollLimit 2 `
            -SleepAction { param($seconds) } | Out-Null
    } catch {
        $protocol = $_.Exception
    }
    Assert-True ($null -ne $protocol) 'undocumented status must throw'
    Assert-Equal 'VcfBundleProtocolException' $protocol.GetType().Name 'undocumented status must be a protocol error'
    Assert-Equal 'WAITING_FOR_DEPOT' $protocol.TaskStatus 'protocol error must retain normalized undocumented status'

    $requests = @(
        Get-Content -LiteralPath $logFile |
            Where-Object { $_ } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
    Assert-True ($requests.Count -ge 8) 'request log must contain authentication, collection, mutation, and poll calls'
    Assert-True (-not (@($requests | Where-Object { $null -eq $_.operationId }).Count)) 'mock must receive only contract-named operations'

    $bundleGets = @($requests | Where-Object operationId -eq 'getBundles')
    Assert-Equal 2 $bundleGets.Count 'client must perform both collection reads through getBundles'

    $patches = @($requests | Where-Object operationId -eq 'startBundleDownloadByID')
    Assert-Equal 4 $patches.Count 'client must submit every requested download through the contract operation'
    foreach ($patch in $patches) {
        Assert-Equal 'application/json' ([string]$patch.contentType).Split(';')[0] 'download request must be JSON'
        Assert-True ($patch.body.bundleDownloadSpec.downloadNow -eq $true) 'downloadNow must be true'
        Assert-Equal 1 @($patch.body.bundleDownloadSpec.PSObject.Properties).Count 'download request must omit unsupplied optional fields'
    }

    $successPolls = @(
        $requests |
            Where-Object {
                $_.operationId -eq 'getTask' -and
                $_.path -eq '/v1/tasks/task-download-001'
            }
    )
    Assert-Equal 2 $successPolls.Count 'accepted success task must be GET-polled until terminal'
    $failurePolls = @(
        $requests |
            Where-Object {
                $_.operationId -eq 'getTask' -and
                $_.path -eq '/v1/tasks/task-download-fail'
            }
    )
    Assert-Equal 1 $failurePolls.Count 'accepted failed task must still be GET-polled'
    $stallPolls = @(
        $requests |
            Where-Object {
                $_.operationId -eq 'getTask' -and
                $_.path -eq '/v1/tasks/task-download-stall'
            }
    )
    Assert-Equal 2 $stallPolls.Count 'PollLimit must count GET polls before timeout'
    $weirdPolls = @(
        $requests |
            Where-Object {
                $_.operationId -eq 'getTask' -and
                $_.path -eq '/v1/tasks/task-download-weird'
            }
    )
    Assert-Equal 1 $weirdPolls.Count 'undocumented terminal state must stop polling immediately'

    foreach ($request in $requests | Where-Object operationId -ne 'createToken') {
        Assert-True (
            [string]$request.authorization -like 'Bearer *'
        ) "SDK request $($request.operationId) must carry bearer authentication"
    }
} finally {
    if ($null -ne $mock -and -not $mock.HasExited) {
        Stop-Process -Id $mock.Id -Force -ErrorAction SilentlyContinue
        $mock.WaitForExit()
    }
    Remove-Item -LiteralPath $portFile, $logFile -Force -ErrorAction SilentlyContinue
}

Write-Host "ALL TESTS PASSED ($script:Passed assertions)"
