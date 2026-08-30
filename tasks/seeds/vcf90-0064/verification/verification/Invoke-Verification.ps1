#Requires -Version 7.2
<#
    Protected verification for the VCF 9.0 Operations maintenance-window module.

    Boots verification/contract_mock.py -- a loopback VCF Operations service
    pinned to docs/contract.json -- on 127.0.0.1, drives
    module/VcfOpsMaintenanceWindow through the genuine VMware.Sdk.Vcf.Ops
    PowerCLI cmdlets, then reads the mock's JSON Lines request log and asserts
    the exact wire shape of every request the module made.

    No live VMware endpoint is contacted.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$script:RepoRoot      = Split-Path -Parent $PSScriptRoot
$script:MockScript    = Join-Path $PSScriptRoot 'contract_mock.py'
$script:ContractPath  = Join-Path $script:RepoRoot 'docs/contract.json'
$script:SourcesPath   = Join-Path $script:RepoRoot 'docs/official_sources.json'
$script:ModuleDir     = Join-Path $script:RepoRoot 'module/VcfOpsMaintenanceWindow'
$script:ModuleManifest = Join-Path $script:ModuleDir 'VcfOpsMaintenanceWindow.psd1'
$script:ModuleSource  = Join-Path $script:ModuleDir 'VcfOpsMaintenanceWindow.psm1'

# Fixtures. These mirror the constants in contract_mock.py.
$script:Username     = 'svc-maintenance'
$script:Password     = 'VMw@re123!Ops'
$script:SessionToken = '0b7c4e1a9d3f4a2b8c6e5d0f1a2b3c4d::7e19'
$script:AuthPrefix   = 'OpsToken '

# Provenance the contract is pinned to.
$script:ExpectedTag = '9.0.0.0'
$script:ExpectedSha = '85151f6b1bb58f13b6ac0304bfec53904bea085f'
$script:RevisionSha = '3949fc33339fc5ea1b77eadb258f1cf49aa88e26'   # 9.1.0.0 -- not this contract
$script:ExpectedSpecPath = 'specifications/vcf-operations/vcf-operations-openapi.json'
$script:ExpectedOperationIds = @(
    'acquireToken'
    'getCurrentVersionOfServer'
    'getMaintenanceSchedules'
    'createMaintenanceSchedules'
    'updateMaintenanceSchedules'
)

$script:Failures = [System.Collections.Generic.List[string]]::new()
$script:Checks = 0
$script:Case = 'startup'

# ---------------------------------------------------------------- assertions

function Add-Failure([string] $Message) {
    $script:Failures.Add("[$script:Case] $Message")
}

function Assert-True([bool] $Condition, [string] $Message) {
    $script:Checks++
    if (-not $Condition) { Add-Failure $Message }
}

function Assert-Equal($Expected, $Actual, [string] $Message) {
    $script:Checks++
    if ($Expected -ne $Actual) {
        Add-Failure ("$Message (expected '$Expected', got '$Actual')")
    }
}

function Assert-SetEqual([string[]] $Expected, [string[]] $Actual, [string] $Message) {
    $script:Checks++
    $e = @($Expected | Sort-Object) -join ', '
    $a = @($Actual   | Sort-Object) -join ', '
    if ($e -ne $a) {
        Add-Failure ("$Message (expected {$e}, got {$a})")
    }
}

function Assert-SequenceEqual([string[]] $Expected, [string[]] $Actual, [string] $Message) {
    $script:Checks++
    $e = @($Expected) -join ' -> '
    $a = @($Actual)   -join ' -> '
    if ($e -ne $a) {
        Add-Failure ("$Message (expected [$e], got [$a])")
    }
}

# ---------------------------------------------------------------- utilities

function Get-MemberNames($Object) {
    if ($null -eq $Object) { return ,@() }
    return ,@($Object.PSObject.Properties | ForEach-Object { $_.Name })
}

function Get-Value($Object, [string] $Name) {
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Get-BodyKeys($Entry) {
    $names = Get-MemberNames $Entry.bodyJson
    return ,$names
}

function Select-Operation($Log, [string] $OperationId) {
    return ,@($Log | Where-Object { $_.operationId -eq $OperationId })
}

# Operations the module itself issues; the two that Connect-VcfOpsServer
# performs are filtered out so a case can assert its own call sequence.
function Select-ModuleOperations($Log) {
    return ,@($Log | Where-Object {
        $_.operationId -notin @('acquireToken', 'getCurrentVersionOfServer')
    })
}

# ---------------------------------------------------------------- mock

function Start-ContractMock {
    $workDir = Join-Path ([System.IO.Path]::GetTempPath()) ("vcfops-verify-" + [guid]::NewGuid().ToString('n'))
    New-Item -ItemType Directory -Path $workDir -Force | Out-Null
    $logPath  = Join-Path $workDir 'requests.jsonl'
    $portPath = Join-Path $workDir 'port.txt'

    $process = Start-Process -FilePath 'python3' -PassThru -NoNewWindow -ArgumentList @(
        '-B', $script:MockScript,
        '--contract', $script:ContractPath,
        '--log', $logPath,
        '--port-file', $portPath
    )

    $deadline = [datetime]::UtcNow.AddSeconds(30)
    while ([datetime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath $portPath) {
            $text = (Get-Content -LiteralPath $portPath -Raw).Trim()
            if ($text -match '^\d+$') {
                return [pscustomobject] @{
                    Process = $process
                    Port    = [int] $text
                    LogPath = $logPath
                    WorkDir = $workDir
                }
            }
        }
        if ($process.HasExited) {
            throw "contract_mock.py exited with code $($process.ExitCode) before it began listening."
        }
        Start-Sleep -Milliseconds 50
    }
    throw 'contract_mock.py did not report a listening port within 30 seconds.'
}

function Stop-ContractMock($Mock) {
    if ($null -eq $Mock) { return }
    if (-not $Mock.Process.HasExited) {
        $Mock.Process.Kill()
        $Mock.Process.WaitForExit(10000) | Out-Null
    }
}

function Get-RequestLog($Mock) {
    if (-not (Test-Path -LiteralPath $Mock.LogPath)) { return ,@() }
    $lines = Get-Content -LiteralPath $Mock.LogPath | Where-Object { $_.Trim().Length -gt 0 }
    return ,@($lines | ForEach-Object { $_ | ConvertFrom-Json })
}

function New-Connection($Mock) {
    return Connect-VcfOpsServer `
        -Server '127.0.0.1' `
        -Port $Mock.Port `
        -Protocol 'http' `
        -User $script:Username `
        -Password (ConvertTo-SecureString $script:Password -AsPlainText -Force) `
        -NotDefault `
        -WarningAction SilentlyContinue `
        -ErrorAction Stop
}

function Close-Connection($Connection) {
    if ($null -eq $Connection) { return }
    try {
        Disconnect-VcfOpsServer -Server $Connection -Confirm:$false `
            -WarningAction SilentlyContinue -ErrorAction SilentlyContinue | Out-Null
    } catch {
        # A closed session is not a verification concern.
    }
}

# ---------------------------------------------------------------- shared checks

function Assert-NoOffContractRequests($Log) {
    $stray = @($Log | Where-Object { $_.offContract })
    Assert-Equal 0 $stray.Count (
        'the module must not call anything outside the operations docs/contract.json names' +
        $(if ($stray.Count) { ' (saw ' + (@($stray | ForEach-Object { $_.method + ' ' + $_.path }) -join ', ') + ')' } else { '' }))
}

function Assert-Authorization($Log) {
    foreach ($entry in $Log) {
        $names = @(Get-MemberNames $entry.headers)
        if ($entry.operationId -eq 'acquireToken') {
            Assert-True (-not ($names -contains 'authorization')) (
                'acquireToken mints the token and must not present an Authorization header')
            continue
        }
        $presented = Get-Value $entry.headers 'authorization'
        Assert-Equal ($script:AuthPrefix + $script:SessionToken) $presented (
            "$($entry.operationId) must carry the acquired session token in the Authorization header")
    }
}

function Assert-JsonContentType($Entry, [string] $What) {
    $ct = Get-Value $Entry.headers 'content-type'
    Assert-True ($null -ne $ct -and $ct -match '^application/json') (
        "$What must be sent as application/json (got '$ct')")
}

function Assert-LookupByKey($Entry, [string] $Key) {
    Assert-Equal 'GET' $Entry.method 'getMaintenanceSchedules must be a GET'
    Assert-Equal '/suite-api/api/maintenanceschedules' $Entry.path (
        'getMaintenanceSchedules must target the path the contract names')
    Assert-SetEqual @('name') (Get-MemberNames $Entry.query) (
        'the lookup must filter server-side with the name parameter alone; no other query parameter belongs on it')
    $values = @(Get-Value $Entry.query 'name')
    Assert-SequenceEqual @($Key) $values (
        'the lookup must ask for exactly the key being reconciled')
}

# The four members every schedule body must carry, per the pinned schema.
$script:RequiredScheduleMembers = @('scheduleType', 'duration', 'hour', 'minuteOfTheHour')
# Optional members that must never appear unless the caller supplied them.
$script:OptionalScheduleMembers = @(
    'dayOfTheMonth', 'daysOfTheMonth', 'daysOfTheWeek', 'expirationDate',
    'expireRuns', 'month', 'months', 'recurrence', 'startDate', 'timeZone',
    'weeksOfTheMonth'
)

function Assert-ScheduleBody($Entry, [string[]] $ExpectedOptional, [hashtable] $Values, [string] $What) {
    $schedule = Get-Value $Entry.bodyJson 'schedule'
    $expected = @($script:RequiredScheduleMembers) + @($ExpectedOptional)
    Assert-SetEqual $expected (Get-MemberNames $schedule) (
        "$What schedule must carry exactly the required members plus the optional ones the caller supplied")

    foreach ($name in $Values.Keys) {
        $actual = Get-Value $schedule $name
        if ($Values[$name] -is [array]) {
            Assert-SequenceEqual @($Values[$name]) @($actual) "$What schedule.$name"
        } else {
            Assert-Equal $Values[$name] $actual "$What schedule.$name"
        }
    }

    # An optional member the caller did not supply must be absent outright --
    # not null, not '', not [], not a zero-valued default.
    $present = @(Get-MemberNames $schedule)
    foreach ($name in $script:OptionalScheduleMembers) {
        if ($ExpectedOptional -contains $name) { continue }
        if ($present -contains $name) {
            $raw = Get-Value $schedule $name
            $rendered = if ($null -eq $raw) { 'null' } else { ($raw | ConvertTo-Json -Compress -Depth 5) }
            Assert-True $false (
                "$What schedule.$name was not supplied by the caller and must be omitted from the body, but it was sent as $rendered")
        } else {
            Assert-True $true "$What schedule.$name is correctly omitted"
        }
    }
}

# ---------------------------------------------------------------- cases

function Invoke-CaseA {
    $script:Case = 'A/create-on-empty'
    $mock = $null
    $connection = $null
    try {
        $mock = Start-ContractMock
        $connection = New-Connection $mock
        $result = Set-VcfOpsMaintenanceSchedule -Server $connection `
            -Key 'vcf-ops-nightly' -Hour 2 -MinuteOfTheHour 30 `
            -DurationMinutes 120 -ScheduleType 'DAILY'
    } finally {
        Close-Connection $connection
        Stop-ContractMock $mock
    }

    $log = Get-RequestLog $mock
    Assert-NoOffContractRequests $log
    Assert-Authorization $log

    $module = Select-ModuleOperations $log
    Assert-SequenceEqual @('getMaintenanceSchedules', 'createMaintenanceSchedules') `
        @($module | ForEach-Object { $_.operationId }) (
        'a first run must look the key up before it creates anything')

    $lookup = Select-Operation $log 'getMaintenanceSchedules'
    if ($lookup.Count -eq 1) { Assert-LookupByKey $lookup[0] 'vcf-ops-nightly' }

    $create = Select-Operation $log 'createMaintenanceSchedules'
    Assert-Equal 1 $create.Count 'exactly one createMaintenanceSchedules request belongs in a first run'
    if ($create.Count -eq 1) {
        $entry = $create[0]
        Assert-Equal 'POST' $entry.method 'createMaintenanceSchedules must be a POST'
        Assert-Equal '/suite-api/api/maintenanceschedules' $entry.path (
            'createMaintenanceSchedules must target the path the contract names')
        Assert-True (-not $entry.hasQueryDelimiter) 'createMaintenanceSchedules takes no query parameters'
        Assert-JsonContentType $entry 'createMaintenanceSchedules'
        Assert-SetEqual @('key', 'schedule') (Get-BodyKeys $entry) (
            'the create body carries key and schedule only; id is assigned by the server and must not be sent')
        Assert-Equal 'vcf-ops-nightly' (Get-Value $entry.bodyJson 'key') 'create body key'
        Assert-ScheduleBody $entry @() @{
            scheduleType    = 'DAILY'
            duration        = 120
            hour            = 2
            minuteOfTheHour = 30
        } 'createMaintenanceSchedules'
    }

    Assert-Equal 'Created' $result.Outcome 'a key that does not exist yet is Created'
    Assert-Equal 'vcf-ops-nightly' $result.Key 'the result reports the key it reconciled'
    Assert-True (-not [string]::IsNullOrWhiteSpace($result.ScheduleId)) (
        'the result must report the server-assigned schedule id')
    Assert-SequenceEqual @('Key', 'ScheduleId', 'Outcome') (Get-MemberNames $result) (
        'the result object exposes Key, ScheduleId and Outcome, in that order')
}

function Invoke-CaseB {
    $script:Case = 'B/retry-is-not-a-second-create'
    $mock = $null
    $connection = $null
    try {
        $mock = Start-ContractMock
        $connection = New-Connection $mock
        $first = Set-VcfOpsMaintenanceSchedule -Server $connection `
            -Key 'vcf-ops-nightly' -Hour 2 -MinuteOfTheHour 30 `
            -DurationMinutes 120 -ScheduleType 'DAILY' -TimeZone 'Europe/Sofia'
        $second = Set-VcfOpsMaintenanceSchedule -Server $connection `
            -Key 'vcf-ops-nightly' -Hour 2 -MinuteOfTheHour 30 `
            -DurationMinutes 120 -ScheduleType 'DAILY' -TimeZone 'Europe/Sofia'
        $third = Set-VcfOpsMaintenanceSchedule -Server $connection `
            -Key 'vcf-ops-nightly' -Hour 2 -MinuteOfTheHour 30 `
            -DurationMinutes 120 -ScheduleType 'DAILY' -TimeZone 'Europe/Sofia'
    } finally {
        Close-Connection $connection
        Stop-ContractMock $mock
    }

    $log = Get-RequestLog $mock
    Assert-NoOffContractRequests $log
    Assert-Authorization $log

    Assert-Equal 'Created' $first.Outcome  'the first run creates the schedule'
    Assert-Equal 'Unchanged' $second.Outcome 'an identical rerun changes nothing'
    Assert-Equal 'Unchanged' $third.Outcome  'and keeps changing nothing however often it runs'
    Assert-Equal $first.ScheduleId $second.ScheduleId 'a rerun reports the same schedule id'
    Assert-Equal $first.ScheduleId $third.ScheduleId  'a rerun reports the same schedule id'
    Assert-SequenceEqual @('Key', 'ScheduleId', 'Outcome') (Get-MemberNames $second) (
        'an Unchanged result exposes Key, ScheduleId and Outcome, in that order')

    # The effect happened exactly once.
    Assert-Equal 1 (Select-Operation $log 'createMaintenanceSchedules').Count (
        'the schedule must be created exactly once no matter how many times the run is repeated')
    Assert-Equal 0 (Select-Operation $log 'updateMaintenanceSchedules').Count (
        'a run that finds the desired state already in place must not write at all')
    Assert-Equal 3 (Select-Operation $log 'getMaintenanceSchedules').Count (
        'every run looks the key up first')

    Assert-SequenceEqual @(
        'getMaintenanceSchedules', 'createMaintenanceSchedules',
        'getMaintenanceSchedules',
        'getMaintenanceSchedules'
    ) @((Select-ModuleOperations $log) | ForEach-Object { $_.operationId }) (
        'the second and third runs must be a lookup and nothing more')

    # No request was ever rejected: a duplicate POST would have drawn the 422
    # that the pinned specification defines for a key that already exists.
    $create = Select-Operation $log 'createMaintenanceSchedules'
    if ($create.Count -ge 1) {
        Assert-Equal 0 $create[0].storeCount (
            'the create must be issued against a service that does not hold the key yet')
        Assert-ScheduleBody $create[0] @('timeZone') @{
            scheduleType    = 'DAILY'
            duration        = 120
            hour            = 2
            minuteOfTheHour = 30
            timeZone        = 'Europe/Sofia'
        } 'createMaintenanceSchedules'
    }

    # The service is holding exactly one schedule when the last run looks.
    $lookups = Select-Operation $log 'getMaintenanceSchedules'
    if ($lookups.Count -eq 3) {
        Assert-Equal 0 $lookups[0].storeCount 'the service starts empty'
        Assert-Equal 1 $lookups[1].storeCount 'after the first run the service holds one schedule'
        Assert-Equal 1 $lookups[2].storeCount 'and it still holds exactly one'
    }
}

function Invoke-CaseC {
    $script:Case = 'C/drift-is-an-update'
    $mock = $null
    $connection = $null
    try {
        $mock = Start-ContractMock
        $connection = New-Connection $mock
        $created = Set-VcfOpsMaintenanceSchedule -Server $connection `
            -Key 'vcf-ops-patching' -Hour 1 -MinuteOfTheHour 0 `
            -DurationMinutes 60 -ScheduleType 'DAILY'
        $updated = Set-VcfOpsMaintenanceSchedule -Server $connection `
            -Key 'vcf-ops-patching' -Hour 1 -MinuteOfTheHour 0 `
            -DurationMinutes 180 -ScheduleType 'DAILY'
        $settled = Set-VcfOpsMaintenanceSchedule -Server $connection `
            -Key 'vcf-ops-patching' -Hour 1 -MinuteOfTheHour 0 `
            -DurationMinutes 180 -ScheduleType 'DAILY'
    } finally {
        Close-Connection $connection
        Stop-ContractMock $mock
    }

    $log = Get-RequestLog $mock
    Assert-NoOffContractRequests $log
    Assert-Authorization $log

    Assert-Equal 'Created' $created.Outcome 'the first run creates'
    Assert-Equal 'Updated' $updated.Outcome 'a run whose desired state differs updates'
    Assert-Equal 'Unchanged' $settled.Outcome 'and the run after that has nothing left to do'
    Assert-Equal $created.ScheduleId $updated.ScheduleId 'an update keeps the existing schedule id'
    Assert-Equal $created.ScheduleId $settled.ScheduleId 'and so does the settled rerun'
    Assert-SequenceEqual @('Key', 'ScheduleId', 'Outcome') (Get-MemberNames $updated) (
        'an Updated result exposes Key, ScheduleId and Outcome, in that order')

    Assert-Equal 1 (Select-Operation $log 'createMaintenanceSchedules').Count (
        'drift is corrected with an update, never with a second create')

    $update = Select-Operation $log 'updateMaintenanceSchedules'
    Assert-Equal 1 $update.Count 'exactly one updateMaintenanceSchedules request belongs in this case'
    if ($update.Count -eq 1) {
        $entry = $update[0]
        Assert-Equal 'PUT' $entry.method 'updateMaintenanceSchedules must be a PUT'
        Assert-Equal '/suite-api/api/maintenanceschedules' $entry.path (
            'updateMaintenanceSchedules must target the path the contract names')
        Assert-True (-not $entry.hasQueryDelimiter) 'updateMaintenanceSchedules takes no query parameters'
        Assert-JsonContentType $entry 'updateMaintenanceSchedules'
        Assert-SetEqual @('id', 'key', 'schedule') (Get-BodyKeys $entry) (
            'the update body must identify the existing schedule by id alongside key and schedule')
        Assert-Equal $created.ScheduleId (Get-Value $entry.bodyJson 'id') (
            'the update must address the id the lookup returned')
        Assert-Equal 'vcf-ops-patching' (Get-Value $entry.bodyJson 'key') 'update body key'
        Assert-ScheduleBody $entry @() @{
            scheduleType    = 'DAILY'
            duration        = 180
            hour            = 1
            minuteOfTheHour = 0
        } 'updateMaintenanceSchedules'
    }
}

function Invoke-CaseD {
    $script:Case = 'D/unset-optionals-are-omitted'
    $mock = $null
    $connection = $null
    try {
        $mock = Start-ContractMock
        $connection = New-Connection $mock
        $result = Set-VcfOpsMaintenanceSchedule -Server $connection `
            -Key 'vcf-ops-weekend' -Hour 23 -MinuteOfTheHour 15 `
            -DurationMinutes 240 -ScheduleType 'WEEKLY' `
            -Recurrence 2 -DaysOfTheWeek @('SATURDAY', 'SUNDAY')
    } finally {
        Close-Connection $connection
        Stop-ContractMock $mock
    }

    $log = Get-RequestLog $mock
    Assert-NoOffContractRequests $log
    Assert-Authorization $log
    Assert-Equal 'Created' $result.Outcome 'a weekly window on an empty service is Created'

    $create = Select-Operation $log 'createMaintenanceSchedules'
    Assert-Equal 1 $create.Count 'exactly one create belongs in this case'
    if ($create.Count -eq 1) {
        Assert-SetEqual @('key', 'schedule') (Get-BodyKeys $create[0]) (
            'the create body carries key and schedule only')
        # recurrence and daysOfTheWeek were supplied; the other nine optional
        # members of the schedule schema were not and must not reach the wire.
        Assert-ScheduleBody $create[0] @('recurrence', 'daysOfTheWeek') @{
            scheduleType    = 'WEEKLY'
            duration        = 240
            hour            = 23
            minuteOfTheHour = 15
            recurrence      = 2
            daysOfTheWeek   = @('SATURDAY', 'SUNDAY')
        } 'createMaintenanceSchedules'

        # Belt and braces, straight off the raw bytes.
        $raw = $create[0].bodyText
        foreach ($name in @('timeZone', 'expirationDate', 'expireRuns', 'startDate',
                            'month', 'months', 'dayOfTheMonth', 'daysOfTheMonth',
                            'weeksOfTheMonth')) {
            Assert-True (-not ($raw -match ('"' + [regex]::Escape($name) + '"'))) (
                "the raw create body must not mention $name at all")
        }
    }
}

function Invoke-CaseE {
    $script:Case = 'E/reconciles-the-right-schedule'
    $mock = $null
    $connection = $null
    try {
        $mock = Start-ContractMock
        $connection = New-Connection $mock
        $other = Set-VcfOpsMaintenanceSchedule -Server $connection `
            -Key 'vcf-ops-nightly-archive' -Hour 4 -MinuteOfTheHour 0 `
            -DurationMinutes 30 -ScheduleType 'DAILY'
        $target = Set-VcfOpsMaintenanceSchedule -Server $connection `
            -Key 'vcf-ops-nightly' -Hour 2 -MinuteOfTheHour 0 `
            -DurationMinutes 90 -ScheduleType 'DAILY'
        $drifted = Set-VcfOpsMaintenanceSchedule -Server $connection `
            -Key 'vcf-ops-nightly' -Hour 3 -MinuteOfTheHour 0 `
            -DurationMinutes 90 -ScheduleType 'DAILY'
    } finally {
        Close-Connection $connection
        Stop-ContractMock $mock
    }

    $log = Get-RequestLog $mock
    Assert-NoOffContractRequests $log
    Assert-Authorization $log

    Assert-Equal 'Created' $other.Outcome  'the near-match schedule is created'
    Assert-Equal 'Created' $target.Outcome (
        'a returned near match is not the exact key and must not be updated as though it were the target')
    Assert-Equal 'Updated' $drifted.Outcome 'the drifted rerun updates'
    Assert-True ($other.ScheduleId -ne $target.ScheduleId) (
        'two different keys must not resolve to the same schedule id')

    Assert-Equal 2 (Select-Operation $log 'createMaintenanceSchedules').Count (
        'two distinct keys mean two creates')

    foreach ($entry in (Select-Operation $log 'getMaintenanceSchedules')) {
        Assert-SetEqual @('name') (Get-MemberNames $entry.query) (
            'every lookup filters by name server-side rather than listing every schedule and sifting locally')
    }

    $update = Select-Operation $log 'updateMaintenanceSchedules'
    if ($update.Count -eq 1) {
        Assert-Equal $target.ScheduleId (Get-Value $update[0].bodyJson 'id') (
            'the update must address the schedule that carries the key being reconciled, not whichever one came back first')
        Assert-Equal 'vcf-ops-nightly' (Get-Value $update[0].bodyJson 'key') (
            'the update must carry the key being reconciled')
    } else {
        Assert-Equal 1 $update.Count 'exactly one update belongs in this case'
    }
}

function Invoke-CaseF {
    $script:Case = 'F/provenance-and-transport'

    $contract = Get-Content -LiteralPath $script:ContractPath -Raw | ConvertFrom-Json
    $sources  = Get-Content -LiteralPath $script:SourcesPath  -Raw | ConvertFrom-Json

    Assert-Equal $script:ExpectedTag $contract.derivedFrom.repositoryTag (
        'the contract is pinned to the 9.0.0.0 tag')
    Assert-Equal $script:ExpectedSha $contract.derivedFrom.repositoryCommitSha (
        'the contract is pinned to that tag''s commit')
    Assert-Equal $script:ExpectedSpecPath $contract.derivedFrom.specPath (
        'the contract is projected from the VCF Operations OpenAPI document')
    Assert-Equal 'vmware/vcf-api-specs' $contract.derivedFrom.repository 'contract repository'
    Assert-Equal 'openapi-specification' $sources.sourceKind (
        'the contract comes from the specification, not from a documentation page')
    Assert-True (-not $sources.documentationPageUsedAsContractSource) (
        'no documentation page may stand in for the specification')

    Assert-Equal $script:ExpectedTag $sources.repositoryTag 'official_sources records the tag'
    Assert-Equal $script:ExpectedSha $sources.repositoryCommitSha 'official_sources records that tag''s commit sha'
    Assert-Equal $script:ExpectedSpecPath $sources.specPath 'official_sources records the spec path'
    Assert-True ($sources.repositoryCommitSha -ne $script:RevisionSha) (
        'the 9.1.0.0 revision of the same file is not the basis of this contract')

    Assert-SetEqual $script:ExpectedOperationIds @($sources.operationIds) (
        'official_sources records every operationId the contract names')
    Assert-SetEqual $script:ExpectedOperationIds @($contract.operations | ForEach-Object { $_.operationId }) (
        'the contract names exactly the operations in scope')
    foreach ($op in $sources.operations) {
        Assert-True (-not [string]::IsNullOrWhiteSpace($op.specJsonPointer)) (
            "$($op.operationId) must be recorded with the JSON pointer it was read from")
        Assert-Equal $script:ExpectedSha $op.repositoryCommitSha (
            "$($op.operationId) must be pinned to the recorded commit")
    }

    # The module must go through the SDK, not around it.
    $source = Get-Content -LiteralPath $script:ModuleSource -Raw
    foreach ($banned in @(
        'Invoke-RestMethod', 'Invoke-WebRequest', 'System.Net.Http',
        'HttpClient', 'WebClient', 'System.Net.WebRequest', 'curl ', 'System.Net.Sockets')) {
        Assert-True (-not ($source -match [regex]::Escape($banned))) (
            "the module must drive the SDK rather than replace its transport (found '$banned')")
    }
    Assert-True ($source -match 'VMware\.Sdk\.Vcf\.Ops') (
        'the module must import VMware.Sdk.Vcf.Ops')
    foreach ($cmdlet in @(
        'Invoke-VcfOpsGetMaintenanceSchedules',
        'Invoke-VcfOpsCreateMaintenanceSchedules',
        'Invoke-VcfOpsUpdateMaintenanceSchedules')) {
        Assert-True ($source -match [regex]::Escape($cmdlet)) (
            "the module must reach the service through $cmdlet")
    }
}

function Invoke-CaseG {
    $script:Case = 'G/all-optionals-and-removal'
    $mock = $null
    $connection = $null
    try {
        $mock = Start-ContractMock
        $connection = New-Connection $mock

        $allOptional = @{
            Server          = $connection
            Key             = 'vcf-ops-full-schedule'
            Hour            = 6
            MinuteOfTheHour = 45
            DurationMinutes = 75
            ScheduleType    = 'WEEKLY'
            Recurrence      = 3
            DaysOfTheWeek   = @('MONDAY', 'FRIDAY')
            StartDate       = '2026-08-17T06:45:00Z'
            ExpirationDate  = '2026-12-31T23:59:00Z'
            ExpireRuns      = 0
            TimeZone        = 'America/Chicago'
        }

        $created = Set-VcfOpsMaintenanceSchedule @allOptional
        $same = Set-VcfOpsMaintenanceSchedule @allOptional

        # The service now holds all six exposed optional members. Omitting them
        # on the next invocation is drift: the update must remove them rather
        # than treating an absent desired member as equal to a stored member.
        $pruned = Set-VcfOpsMaintenanceSchedule -Server $connection `
            -Key 'vcf-ops-full-schedule' -Hour 6 -MinuteOfTheHour 45 `
            -DurationMinutes 75 -ScheduleType 'WEEKLY'
        $settled = Set-VcfOpsMaintenanceSchedule -Server $connection `
            -Key 'vcf-ops-full-schedule' -Hour 6 -MinuteOfTheHour 45 `
            -DurationMinutes 75 -ScheduleType 'WEEKLY'
    } finally {
        Close-Connection $connection
        Stop-ContractMock $mock
    }

    $log = Get-RequestLog $mock
    Assert-NoOffContractRequests $log
    Assert-Authorization $log

    Assert-Equal 'Created' $created.Outcome 'the first full schedule is created'
    Assert-Equal 'Unchanged' $same.Outcome 'an identical full schedule is unchanged'
    Assert-Equal 'Updated' $pruned.Outcome 'omitting stored optional members removes them with an update'
    Assert-Equal 'Unchanged' $settled.Outcome 'the pruned schedule then remains unchanged'
    Assert-Equal $created.ScheduleId $same.ScheduleId 'the full rerun keeps the existing schedule id'
    Assert-Equal $created.ScheduleId $pruned.ScheduleId 'removing optional members keeps the existing schedule id'
    Assert-Equal $created.ScheduleId $settled.ScheduleId 'the settled pruned run keeps the existing schedule id'

    Assert-SequenceEqual @(
        'getMaintenanceSchedules', 'createMaintenanceSchedules',
        'getMaintenanceSchedules',
        'getMaintenanceSchedules', 'updateMaintenanceSchedules',
        'getMaintenanceSchedules'
    ) @((Select-ModuleOperations $log) | ForEach-Object { $_.operationId }) (
        'all-optionals create, identical retry, removal update and settled retry use the expected calls')

    $create = Select-Operation $log 'createMaintenanceSchedules'
    Assert-Equal 1 $create.Count 'the all-optionals case creates exactly once'
    if ($create.Count -eq 1) {
        Assert-ScheduleBody $create[0] @(
            'recurrence', 'daysOfTheWeek', 'startDate', 'expirationDate',
            'expireRuns', 'timeZone'
        ) @{
            scheduleType    = 'WEEKLY'
            duration        = 75
            hour            = 6
            minuteOfTheHour = 45
            recurrence      = 3
            daysOfTheWeek   = @('MONDAY', 'FRIDAY')
            startDate       = '2026-08-17T06:45:00Z'
            expirationDate  = '2026-12-31T23:59:00Z'
            expireRuns      = 0
            timeZone        = 'America/Chicago'
        } 'createMaintenanceSchedules'
    }

    $update = Select-Operation $log 'updateMaintenanceSchedules'
    Assert-Equal 1 $update.Count 'removing stored optional members requires exactly one update'
    if ($update.Count -eq 1) {
        Assert-SetEqual @('id', 'key', 'schedule') (Get-BodyKeys $update[0]) (
            'the optional-removal update carries id, key and schedule')
        Assert-Equal $created.ScheduleId (Get-Value $update[0].bodyJson 'id') (
            'the optional-removal update addresses the id returned by lookup')
        Assert-ScheduleBody $update[0] @() @{
            scheduleType    = 'WEEKLY'
            duration        = 75
            hour            = 6
            minuteOfTheHour = 45
        } 'updateMaintenanceSchedules'
    }
}

# ---------------------------------------------------------------- entry point

if (-not (Get-Command python3 -ErrorAction SilentlyContinue)) {
    Write-Error 'python3 is required to run the loopback contract mock.'
    exit 2
}
if (-not (Get-Module -ListAvailable -Name 'VMware.Sdk.Vcf.Ops')) {
    Write-Error 'VMware.Sdk.Vcf.Ops is an environment prerequisite and must be installed.'
    exit 2
}

Import-Module 'VMware.Sdk.Vcf.Ops' -WarningAction SilentlyContinue -ErrorAction Stop
Import-Module $script:ModuleManifest -Force -WarningAction SilentlyContinue -ErrorAction Stop

$cases = @(
    'Invoke-CaseA', 'Invoke-CaseB', 'Invoke-CaseC', 'Invoke-CaseD',
    'Invoke-CaseE', 'Invoke-CaseF', 'Invoke-CaseG'
)
foreach ($case in $cases) {
    try {
        & $case
    } catch {
        $script:Checks++
        Add-Failure "the case threw instead of completing: $($_.Exception.Message)"
    }
}

$script:Case = 'summary'
Write-Host ''
if ($script:Failures.Count -eq 0) {
    Write-Host "PASS - $script:Checks assertions across $($cases.Count) cases." -ForegroundColor Green
    exit 0
}
Write-Host "FAIL - $($script:Failures.Count) of $script:Checks assertions failed:" -ForegroundColor Red
foreach ($failure in $script:Failures) {
    Write-Host "  - $failure" -ForegroundColor Red
}
exit 1
