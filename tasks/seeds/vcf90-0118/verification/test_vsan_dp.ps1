# Protected acceptance harness for the VsanDataProtection module.
# It starts a loopback-only contract fixture and inspects its request log.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$script:Checks = 0
$script:Failures = 0

function Assert-True {
    param([string] $Label, [bool] $Condition)
    $script:Checks++
    if ($Condition) { return }
    $script:Failures++
    Write-Output "FAIL $Label"
}

function Assert-Equal {
    param([string] $Label, $Expected, $Actual)
    $script:Checks++
    if ("$Expected" -ceq "$Actual") { return }
    $script:Failures++
    Write-Output "FAIL $Label"
    Write-Output "  expected: $Expected"
    Write-Output "  actual:   $Actual"
}

function Assert-Absent {
    param([string] $Label, $Value)
    Assert-True $Label ($null -eq $Value)
}

function Start-TestFixture {
    param(
        [Parameter(Mandatory)]
        [string] $Work,

        [Parameter(Mandatory)]
        [string] $Scenario
    )

    Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $Work > $null
    $portFile = Join-Path $Work 'port.json'
    $requestLog = Join-Path $Work 'requests.json'
    $stdout = Join-Path $Work 'server.out'
    $stderr = Join-Path $Work 'server.err'
    $server = Start-Process -FilePath 'python3' `
        -ArgumentList @(
            (Join-Path $PSScriptRoot 'mock_vsan_dp.py'),
            $portFile,
            $requestLog,
            $Scenario
        ) `
        -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr

    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(20)
        while (-not (Test-Path -LiteralPath $portFile -PathType Leaf)) {
            if ($server.HasExited -or [DateTime]::UtcNow -gt $deadline) {
                $details = Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue
                throw "loopback fixture failed to start: $details"
            }
            Start-Sleep -Milliseconds 40
        }
        $port = Get-Content -LiteralPath $portFile -Raw | ConvertFrom-Json
        [pscustomobject]@{
            Server = $server
            BaseUrl = "http://127.0.0.1:$port"
            RequestLog = $requestLog
            Work = $Work
        }
    } catch {
        if (-not $server.HasExited) {
            Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
            $server.WaitForExit()
        }
        Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
        throw
    }
}

function Stop-TestFixture {
    param($Fixture)
    if ($null -eq $Fixture) { return }
    if (-not $Fixture.Server.HasExited) {
        Stop-Process -Id $Fixture.Server.Id -Force -ErrorAction SilentlyContinue
        $Fixture.Server.WaitForExit()
    }
    Remove-Item -LiteralPath $Fixture.Work -Recurse -Force -ErrorAction SilentlyContinue
}

$contract = Get-Content -LiteralPath 'docs/contract.json' -Raw | ConvertFrom-Json
$sources = Get-Content -LiteralPath 'docs/official_sources.json' -Raw | ConvertFrom-Json
Assert-Equal 'contract pins VCF 9.0.0.0' '9.0.0.0' $contract.version
Assert-Equal 'source tag is exactly 9.0.0.0' '9.0.0.0' $sources.tag
Assert-Equal 'source commit is pinned' '85151f6b1bb58f13b6ac0304bfec53904bea085f' $sources.tag_commit_sha
Assert-Equal 'source path is the vSAN DP OpenAPI file' `
    'specifications/vsan-data-protection/vsan-data-protection-openapi.yaml' $sources.spec_path

$expectedOperationIds = @(
    'Snapservice.Sessions_create',
    'Snapservice.Clusters.ProtectionGroups_get',
    'Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task',
    'Snapservice.Tasks_get'
)
Assert-Equal 'contract names the exact four operationIds' ($expectedOperationIds -join ',') `
    ((@($contract.operations) | ForEach-Object { $_.operationId }) -join ',')
Assert-Equal 'provenance repeats every operationId' ($expectedOperationIds -join ',') `
    ((@($sources.operations) | ForEach-Object { $_.operationId }) -join ',')

$manifest = Import-PowerShellDataFile -LiteralPath (Join-Path $PSScriptRoot 'VsanDataProtection.psd1')
$requiredNames = @($manifest.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ } else { $_.ModuleName }
})
Assert-True 'manifest keeps the environment-provided VMware.Sdk.Vcf prerequisite' `
    ($requiredNames -ccontains 'VMware.Sdk.Vcf.SddcManager')
Assert-Equal 'manifest exports only the run command' 'Invoke-VsanDpSnapshotRun' `
    (@($manifest.FunctionsToExport) -join ',')

$moduleFile = Join-Path $PSScriptRoot 'VsanDataProtection.psm1'
if (-not (Test-Path -LiteralPath $moduleFile -PathType Leaf)) {
    Write-Output 'FAIL VsanDataProtection.psm1 not found'
    Write-Output "checks=$($script:Checks) failures=$($script:Failures + 1)"
    exit 1
}

$work = Join-Path $PSScriptRoot '_verify'
$server = $null
try {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $work > $null
    $portFile = Join-Path $work 'port.json'
    $requestLog = Join-Path $work 'requests.json'
    $stdout = Join-Path $work 'server.out'
    $stderr = Join-Path $work 'server.err'

    $server = Start-Process -FilePath 'python3' `
        -ArgumentList @((Join-Path $PSScriptRoot 'mock_vsan_dp.py'), $portFile, $requestLog) `
        -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr

    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Path -LiteralPath $portFile -PathType Leaf)) {
        if ($server.HasExited -or [DateTime]::UtcNow -gt $deadline) {
            $details = Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue
            throw "loopback fixture failed to start: $details"
        }
        Start-Sleep -Milliseconds 40
    }

    $port = Get-Content -LiteralPath $portFile -Raw | ConvertFrom-Json
    $baseUrl = "http://127.0.0.1:$port"
    $securePassword = ConvertTo-SecureString 'dummy-pass-0118' -AsPlainText -Force
    $credential = [pscredential]::new('svc-vsandp', $securePassword)

    Import-Module $moduleFile -Force
    $command = Get-Command -Name 'Invoke-VsanDpSnapshotRun' -Module VsanDataProtection
    Assert-True 'run command is exported by the script module' ($null -ne $command)

    $result = Invoke-VsanDpSnapshotRun `
        -BaseUrl $baseUrl `
        -Credential $credential `
        -ClusterId 'domain-c8' `
        -ProtectionGroupId 'pg-nightly' `
        -SnapshotName 'pre-upgrade' `
        -PollIntervalMilliseconds 0

    Assert-Equal 'protection group read is preserved in output' 'nightly-critical' $result.ProtectionGroupName
    Assert-Equal 'snapshot task id is returned' 'task-77' $result.TaskId
    Assert-Equal 'terminal task status is returned' 'SUCCEEDED' $result.Status
    Assert-Equal 'terminal task result survives token refresh' 'snapshot-88' $result.Result.snapshot
    $resultJson = $result | ConvertTo-Json -Depth 8 -Compress
    Assert-True 'output does not expose the password' (-not $resultJson.Contains('dummy-pass-0118'))
    Assert-True 'output does not expose the first session token' (-not $resultJson.Contains('session-1'))
    Assert-True 'output does not expose the replacement session token' (-not $resultJson.Contains('session-2'))

    $requests = @(Get-Content -LiteralPath $requestLog -Raw | ConvertFrom-Json)
    Assert-Equal 'exact request count proves no completed work was replayed' 7 $requests.Count
    if ($requests.Count -eq 7) {
        $expectedSequence = @(
            'POST /api/snapservice/sessions?',
            'GET /api/snapservice/clusters/domain-c8/protection-groups/pg-nightly?',
            'POST /api/snapservice/clusters/domain-c8/protection-groups/pg-nightly/snapshots?vmw-task=true',
            'POST /api/snapservice/sessions?',
            'POST /api/snapservice/clusters/domain-c8/protection-groups/pg-nightly/snapshots?vmw-task=true',
            'GET /api/snapservice/tasks/task-77?',
            'GET /api/snapservice/tasks/task-77?'
        )
        $actualSequence = @($requests | ForEach-Object { "$($_.method) $($_.path)?$($_.raw_query)" })
        Assert-Equal 'operation order refreshes only the interrupted step' `
            ($expectedSequence -join '|') ($actualSequence -join '|')

        $expectedBasic = 'Basic ' + [Convert]::ToBase64String(
            [Text.Encoding]::UTF8.GetBytes('svc-vsandp:dummy-pass-0118')
        )
        foreach ($index in @(0, 3)) {
            Assert-Equal "session request $index uses exact Basic credentials" $expectedBasic $requests[$index].authorization
            Assert-Absent "session request $index omits vmware-api-session-id" $requests[$index].session_id
            Assert-Absent "session request $index omits Content-Type without a requestBody" $requests[$index].content_type
            Assert-Equal "session request $index body is zero bytes" 0 $requests[$index].content_length
            Assert-Equal "session request $index has an empty body" '' $requests[$index].body
        }

        Assert-Equal 'protection-group read uses the first token' 'session-1' $requests[1].session_id
        Assert-Absent 'protection-group read omits Authorization' $requests[1].authorization
        Assert-Absent 'protection-group read omits Content-Type' $requests[1].content_type
        Assert-Equal 'first snapshot attempt uses the expired token' 'session-1' $requests[2].session_id
        Assert-Equal 'snapshot retry uses only the replacement token' 'session-2' $requests[4].session_id
        Assert-Equal 'first task read uses replacement token' 'session-2' $requests[5].session_id
        Assert-Equal 'second task read uses replacement token' 'session-2' $requests[6].session_id

        foreach ($request in $requests) {
            Assert-Equal 'every operation requests JSON' 'application/json' $request.accept
        }

        foreach ($index in @(2, 4)) {
            Assert-True "snapshot request $index has JSON Content-Type" `
                ($requests[$index].content_type -like 'application/json*')
            Assert-Equal "snapshot request $index has exact compact JSON bytes" `
                '7b226e616d65223a227072652d75706772616465227d' $requests[$index].body_hex
            $snapshotBody = $requests[$index].body | ConvertFrom-Json
            Assert-Equal "snapshot request $index contains only the required name" 'name' `
                (($snapshotBody.PSObject.Properties.Name) -join ',')
            Assert-Absent "snapshot request $index omits optional retention" $snapshotBody.PSObject.Properties['retention']
        }

        foreach ($index in @(1, 5, 6)) {
            Assert-Equal "GET request $index has no query" '' $requests[$index].raw_query
            Assert-Equal "GET request $index has zero body bytes" 0 $requests[$index].content_length
            Assert-Equal "GET request $index has an empty body" '' $requests[$index].body
            Assert-Absent "GET request $index omits Content-Type" $requests[$index].content_type
        }
    }
} finally {
    if ($null -ne $server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
        $server.WaitForExit()
    }
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

$failedFixture = $null
try {
    $failedFixture = Start-TestFixture `
        -Work (Join-Path $PSScriptRoot '_verify_failed') `
        -Scenario 'failed_task'
    $failedResult = Invoke-VsanDpSnapshotRun `
        -BaseUrl $failedFixture.BaseUrl `
        -Credential $credential `
        -ClusterId 'domain-c8' `
        -ProtectionGroupId 'pg-nightly' `
        -SnapshotName 'pre-upgrade' `
        -PollIntervalMilliseconds 0

    Assert-Equal 'failed task still returns the protection-group name' `
        'nightly-critical' $failedResult.ProtectionGroupName
    Assert-Equal 'failed task returns its task id' 'task-77' $failedResult.TaskId
    Assert-Equal 'FAILED is treated as a terminal status' 'FAILED' $failedResult.Status
    Assert-Absent 'failed task has a null result when the API omits result' $failedResult.Result
    $failedRequests = @(Get-Content -LiteralPath $failedFixture.RequestLog -Raw | ConvertFrom-Json)
    Assert-Equal 'failed terminal task is read exactly once' 6 $failedRequests.Count
    if ($failedRequests.Count -eq 6) {
        Assert-Equal 'failed run ends with the task read' `
            'GET /api/snapservice/tasks/task-77?' `
            "$($failedRequests[5].method) $($failedRequests[5].path)?$($failedRequests[5].raw_query)"
    }
} finally {
    Stop-TestFixture $failedFixture
}

$sensitiveFixture = $null
try {
    $sensitiveFixture = Start-TestFixture `
        -Work (Join-Path $PSScriptRoot '_verify_sensitive') `
        -Scenario 'sensitive_error'
    $exceptionText = ''
    $didThrow = $false
    try {
        Invoke-VsanDpSnapshotRun `
            -BaseUrl $sensitiveFixture.BaseUrl `
            -Credential $credential `
            -ClusterId 'domain-c8' `
            -ProtectionGroupId 'pg-nightly' `
            -SnapshotName 'pre-upgrade' `
            -PollIntervalMilliseconds 0 > $null
    } catch {
        $didThrow = $true
        $exceptionText = $_.Exception.ToString()
    }

    Assert-True 'non-success HTTP response raises an exception' $didThrow
    Assert-True 'exception does not expose the password from the response body' `
        (-not $exceptionText.Contains('dummy-pass-0118'))
    Assert-True 'exception does not expose the first token from the response body' `
        (-not $exceptionText.Contains('session-1'))
    Assert-True 'exception does not expose the replacement token from the response body' `
        (-not $exceptionText.Contains('session-2'))
    $sensitiveRequests = @(Get-Content -LiteralPath $sensitiveFixture.RequestLog -Raw | ConvertFrom-Json)
    Assert-Equal 'error scenario fails on the refreshed snapshot request' 5 $sensitiveRequests.Count
} finally {
    Stop-TestFixture $sensitiveFixture
}

Write-Output "checks=$($script:Checks) failures=$($script:Failures)"
if ($script:Failures -gt 0) { exit 1 }
Write-Output 'ALL TESTS PASSED'
exit 0
