<#
.SYNOPSIS
    Protected verifier for the VcfOpsAlertTriage module.

.DESCRIPTION
    Runs two scenarios against a loopback mock pinned to docs/contract.json and
    asserts the exact wire shape of every request the module produced.

    Nothing here contacts a VMware endpoint. The only socket opened is a
    127.0.0.1 listener owned by tools/mock/Start-VcfOpsMock.ps1.

    Exit code 0 = pass, 1 = fail.
#>
[CmdletBinding()]
param(
    [string] $ModulePath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot     = Split-Path -Parent $PSScriptRoot
$contractPath = Join-Path $repoRoot 'docs/contract.json'
$mockScript   = Join-Path $repoRoot 'tools/mock/Start-VcfOpsMock.ps1'
$harness      = Join-Path $PSScriptRoot 'harness/Invoke-TriageRun.ps1'
$fixtureDir   = Join-Path $repoRoot 'tools/mock/fixtures'
if (-not $ModulePath) {
    $ModulePath = Join-Path $repoRoot 'src/VcfOpsAlertTriage/VcfOpsAlertTriage.psd1'
}

$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
$corpus   = (Get-Content -LiteralPath (Join-Path $fixtureDir 'scenario-token-expiry.json') -Raw | ConvertFrom-Json).alerts

$script:Failures = @()
$script:Checks   = 0

function Assert-That([bool]$Condition, [string]$Message) {
    $script:Checks++
    if ($Condition) {
        Write-Host ("  [pass] {0}" -f $Message)
    } else {
        $script:Failures += $Message
        Write-Host ("  [FAIL] {0}" -f $Message) -ForegroundColor Red
    }
}

function Assert-Equal($Expected, $Actual, [string]$Message) {
    $e = ($Expected | ConvertTo-Json -Compress -Depth 6)
    $a = ($Actual   | ConvertTo-Json -Compress -Depth 6)
    Assert-That ($e -eq $a) ("{0} (expected {1}, got {2})" -f $Message, $e, $a)
}

# Start-Process joins -ArgumentList with spaces and does no quoting of its own,
# so anything containing whitespace has to be quoted here or it arrives as
# several positional arguments.
function ConvertTo-ProcessArgument([string]$Value) {
    if ($Value -match '[\s"]') { return '"' + ($Value -replace '"', '\"') + '"' }
    return $Value
}

function Get-FreePort {
    $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $l.Start()
    $p = $l.LocalEndpoint.Port
    $l.Stop()
    return $p
}

function Invoke-Scenario {
    param(
        [string]   $Fixture,
        [string]   $Action,
        [int]      $PageSize,
        [switch]   $ActiveOnly,
        [string]   $AuthSource,
        [int]      $SuspendMinutes,
        [string]   $OwnerAccountId,
        [string]   $ResourceKind,
        [string[]] $AlertCriticality
    )

    $work = Join-Path ([System.IO.Path]::GetTempPath()) ("vcfops-verify-" + [guid]::NewGuid().ToString('n'))
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $logPath      = Join-Path $work 'requests.jsonl'
    $readyPath    = Join-Path $work 'ready'
    $boundaryPath = Join-Path $work 'boundary'
    $resultPath   = Join-Path $work 'result.json'
    New-Item -ItemType File -Path $logPath -Force | Out-Null

    $port = Get-FreePort
    $mock = Start-Process -FilePath 'pwsh' -PassThru -ArgumentList @(
        '-NoProfile', '-File', $mockScript,
        '-Port', $port,
        '-FixturePath', (Join-Path $fixtureDir $Fixture),
        '-LogPath', $logPath,
        '-ContractPath', $contractPath,
        '-ReadyPath', $readyPath
    ) -RedirectStandardOutput (Join-Path $work 'mock.out') `
      -RedirectStandardError (Join-Path $work 'mock.err')

    try {
        $deadline = [datetime]::UtcNow.AddSeconds(60)
        while (-not (Test-Path -LiteralPath $readyPath)) {
            if ([datetime]::UtcNow -gt $deadline) { throw "mock did not become ready on port $port" }
            if ($mock.HasExited) { throw "mock exited early with code $($mock.ExitCode)" }
            Start-Sleep -Milliseconds 100
        }

        $harnessArgs = @(
            '-NoProfile', '-File', $harness,
            '-Port', $port,
            '-ModulePath', $ModulePath,
            '-LogPath', $logPath,
            '-BoundaryPath', $boundaryPath,
            '-ResultPath', $resultPath,
            '-Action', $Action
        )
        if ($PSBoundParameters.ContainsKey('PageSize')) { $harnessArgs += @('-PageSize', $PageSize) }
        if ($ActiveOnly)          { $harnessArgs += '-ActiveOnly' }
        if ($AuthSource)         { $harnessArgs += @('-AuthSource', $AuthSource) }
        if ($SuspendMinutes)     { $harnessArgs += @('-SuspendMinutes', $SuspendMinutes) }
        if ($OwnerAccountId)     { $harnessArgs += @('-OwnerAccountId', $OwnerAccountId) }
        if ($ResourceKind)       { $harnessArgs += @('-ResourceKind', $ResourceKind) }
        if ($AlertCriticality)   { $harnessArgs += @('-AlertCriticality', ($AlertCriticality -join ',')) }
        $harnessArgs = @($harnessArgs | ForEach-Object { ConvertTo-ProcessArgument ([string]$_) })

        $stdout = Join-Path $work 'harness.out'
        $stderr = Join-Path $work 'harness.err'
        $run = Start-Process -FilePath 'pwsh' -PassThru -Wait `
            -ArgumentList $harnessArgs -RedirectStandardOutput $stdout -RedirectStandardError $stderr

        $entries = @()
        if (Test-Path -LiteralPath $logPath) {
            $entries = @(Get-Content -LiteralPath $logPath | Where-Object { $_ } | ForEach-Object { $_ | ConvertFrom-Json })
        }
        $boundary = 0
        if (Test-Path -LiteralPath $boundaryPath) { $boundary = [int](Get-Content -LiteralPath $boundaryPath -Raw) }

        return [pscustomobject]@{
            MockErr  = if (Test-Path (Join-Path $work 'mock.err')) { Get-Content -LiteralPath (Join-Path $work 'mock.err') -Raw } else { '' }
            ExitCode = $run.ExitCode
            Stdout   = if (Test-Path $stdout) { Get-Content -LiteralPath $stdout -Raw } else { '' }
            Stderr   = if (Test-Path $stderr) { Get-Content -LiteralPath $stderr -Raw } else { '' }
            All      = $entries
            Module   = @($entries | Where-Object { $_.seq -gt $boundary })
            Result   = if (Test-Path -LiteralPath $resultPath) {
                Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
            } else { $null }
        }
    }
    finally {
        if (-not $mock.HasExited) { Stop-Process -Id $mock.Id -Force -ErrorAction SilentlyContinue }
    }
}

function Get-Keys($Entry) { return @($Entry.bodyKeys | Sort-Object) }

# The property collection is made an array before `Name` is read off it: under
# `Set-StrictMode -Version Latest` member enumeration over an empty collection
# throws instead of yielding nothing, and a request with no query parameters --
# which several assertions below require -- has exactly none.
function Get-QueryKeys($Entry) {
    if ($null -eq $Entry.query) { return ,@() }
    return ,@(@($Entry.query.PSObject.Properties) | ForEach-Object { $_.Name } | Sort-Object)
}

function Get-ActionedId($Entry) {
    # modifyAlerts carries the alert it acted on in the uuids array of its body.
    return @(($Entry.body | ConvertFrom-Json).uuids)
}

# =====================================================================
Write-Host "`n== Scenario 1: token revoked part way through the run ==" -ForegroundColor Cyan

$expectedActiveOrder = @($corpus | Where-Object { $_.status -eq 'ACTIVE' } | ForEach-Object { $_.alertId })
$expectedActive = @($expectedActiveOrder | Sort-Object)
$s1 = Invoke-Scenario -Fixture 'scenario-token-expiry.json' -Action 'suspend' -PageSize 3 -ActiveOnly -SuspendMinutes 60

Assert-That ($s1.ExitCode -eq 0) "the triage run completes without terminating (stderr: $($s1.Stderr)$($s1.MockErr))"
Assert-That ($null -ne $s1.Result) 'the triage run returns a summary object'

$violations = @($s1.All | Where-Object { $_.contractViolation })
Assert-That ($violations.Count -eq 0) `
    ("no request falls outside the contract (saw: {0})" -f (($violations | ForEach-Object { "$($_.method) $($_.path)" }) -join '; '))

$acquires = @($s1.Module | Where-Object { $_.operationId -eq 'acquireToken' })
Assert-Equal 2 $acquires.Count 'the module acquires a token twice: once to start, once after the revocation'

foreach ($a in $acquires) {
    Assert-Equal @('password', 'username') (Get-Keys $a) `
        "acquireToken body carries exactly username and password; the unset optional authSource is omitted, not sent as null or empty (seq $($a.seq))"
    Assert-Equal @() (Get-QueryKeys $a) "acquireToken sends no query parameters (seq $($a.seq))"
    Assert-That ($a.contentType -eq 'application/json') "acquireToken sends application/json (seq $($a.seq))"
    $credentials = $a.body | ConvertFrom-Json
    Assert-That ($credentials.username -eq 'svc-triage') "acquireToken uses the supplied credential username (seq $($a.seq))"
    Assert-That ($credentials.password -eq 'triage-secret') "acquireToken uses the supplied credential password (seq $($a.seq))"
    Assert-That ($a.body -notmatch 'authSource') "acquireToken raw body contains no authSource token at all (seq $($a.seq))"
    Assert-That ($a.body -notmatch ':\s*null') "acquireToken raw body serializes no null-valued field (seq $($a.seq))"
}

$expired = @($s1.Module | Where-Object { $_.status -eq 401 -and $_.note -eq 'token-expired' })
Assert-That ($expired.Count -ge 1) 'the server revokes the token part way through the run'

# The point of the scenario: the request that was in flight when the token died
# must be re-sent byte-for-byte under the new token, not skipped and not mutated.
if ($expired.Count -ge 1) {
    $victim = $expired[0]
    Assert-That ($victim.operationId -eq 'modifyAlerts') `
        "the revocation lands on a unit of work in flight, not on a read (hit $($victim.operationId))"

    $refresh = @($acquires | Where-Object { $_.seq -gt $victim.seq })
    Assert-That ($refresh.Count -eq 1) 'exactly one token refresh follows the revocation'

    $retry = @($s1.Module | Where-Object {
        $_.seq -gt $victim.seq -and
        $_.method -eq $victim.method -and
        $_.path -eq $victim.path -and
        $_.body -eq $victim.body -and
        $_.status -ge 200 -and $_.status -lt 300
    })
    Assert-That ($retry.Count -eq 1) `
        'the request that was rejected is retried once, unchanged, and succeeds -- the in-flight work is not lost'
    if ($retry.Count -eq 1 -and $refresh.Count -eq 1) {
        Assert-That ($retry[0].seq -gt $refresh[0].seq) 'the retry is sent after the refresh, under the new token'
    }
}

# Scoped to the operations the contract marks as requiring authorization.
# acquireToken is not one of them -- it declares an empty security requirement --
# and the SDK stamps the connection's own token onto it regardless, which says
# nothing about how the module handles credentials.
$authorizedOps = @(
    $contract.operations.PSObject.Properties.Name |
        Where-Object { $contract.operations.$_.requiresAuthorization } |
        ForEach-Object { $contract.operations.$_.operationId }
)
$tokensSeen = @($s1.Module |
    Where-Object { $authorizedOps -contains $_.operationId -and $_.authorization } |
    ForEach-Object { $_.authorization } | Sort-Object -Unique)
Assert-That ($tokensSeen -notcontains 'OpsToken ops-token-1') `
    'the module never spends the caller''s bootstrap token on its work; it authenticates with the token it acquired itself'
Assert-That (@($tokensSeen | Where-Object { $_ -notmatch '^OpsToken ops-token-[23]$' }).Count -eq 0) `
    "every authorized request carries 'OpsToken <token>' for a token this run acquired (saw: $($tokensSeen -join ', '))"

$queries = @($s1.Module | Where-Object { $_.operationId -eq 'queryAlert' })
Assert-That ($queries.Count -ge 3) "the alert list is paged through (saw $($queries.Count) queries)"
foreach ($q in $queries) {
    Assert-Equal @('activeOnly') (Get-Keys $q) `
        "queryAlert body carries only activeOnly; the unset alertCriticality and resourceKind are omitted rather than sent as empty arrays or nulls (seq $($q.seq))"
    Assert-That ($q.body -eq '{"activeOnly":true}') "queryAlert body is exactly {`"activeOnly`":true} (seq $($q.seq), got $($q.body))"
    Assert-Equal @('page', 'pageSize') (Get-QueryKeys $q) `
        "queryAlert sends exactly page and pageSize as query parameters (seq $($q.seq))"
    Assert-That ($q.query.PSObject.Properties.Name -contains 'pageSize' -and $q.query.pageSize -eq '3') `
        "queryAlert sends pageSize=3 as a query parameter, not in the body (seq $($q.seq))"
    Assert-That ($q.contentType -eq 'application/json') "queryAlert sends application/json (seq $($q.seq))"
}
$pages = @($queries | ForEach-Object { [int]$_.query.page } | Sort-Object -Unique)
Assert-Equal @(0, 1, 2) @($pages | Where-Object { $_ -le 2 }) 'pages 0, 1 and 2 are each requested'

$actions = @($s1.Module | Where-Object { $_.operationId -eq 'modifyAlerts' -and $_.status -eq 200 })
Assert-Equal 7 $actions.Count 'the action is applied once for each of the 7 active alerts'
$actionedIds = @($actions | ForEach-Object { Get-ActionedId $_ })
Assert-Equal $expectedActive @($actionedIds | Sort-Object) 'the action covers exactly the active alerts'
Assert-Equal $expectedActiveOrder $actionedIds 'the action preserves alert enumeration order'
Assert-Equal $actionedIds.Count @($actionedIds | Sort-Object -Unique).Count `
    'no alert is actioned twice -- the refresh replays the rejected request and nothing else'
foreach ($m in $actions) {
    Assert-Equal @('uuids') (Get-Keys $m) "modifyAlerts body carries only uuids (seq $($m.seq))"
    Assert-Equal 1 @(Get-ActionedId $m).Count "modifyAlerts acts on one alert per request (seq $($m.seq))"
    # The sharp edge: action and minutes were set, userAccountID was not, and
    # page/pageSize were never asked for. Only the first two may appear.
    Assert-Equal @('action', 'minutes') (Get-QueryKeys $m) `
        "modifyAlerts sends exactly action and minutes; the unset userAccountID, page and pageSize are absent from the query string rather than sent empty (seq $($m.seq))"
    Assert-That ($m.contentType -eq 'application/json') "modifyAlerts sends application/json (seq $($m.seq))"
    Assert-That ($m.query.action -eq 'suspend') "the action sent is the one requested (seq $($m.seq))"
    Assert-That ($m.query.minutes -eq '60')     "the suspend window sent is the one requested (seq $($m.seq))"
}

$releases = @($s1.Module | Where-Object { $_.operationId -eq 'releaseToken' })
Assert-Equal 1 $releases.Count 'the run releases its token exactly once'
if ($releases.Count -eq 1) {
    Assert-That ($releases[0].seq -eq ($s1.Module | Measure-Object -Property seq -Maximum).Maximum) `
        'releaseToken is the last request the module makes'
    Assert-That ($releases[0].authorization -eq 'OpsToken ops-token-3') `
        'the token released is the one currently held, not the revoked one'
    Assert-Equal @() (Get-QueryKeys $releases[0]) 'releaseToken sends no query parameters'
    Assert-Equal @() (Get-Keys $releases[0]) 'releaseToken sends no JSON body'
    Assert-That ([string]::IsNullOrEmpty($releases[0].contentType)) 'releaseToken sends no content type for its empty body'
}

if ($s1.Result) {
    Assert-Equal @('ActionedAlertIds', 'AlertsActioned', 'AlertsFound', 'TokensAcquired') `
        @($s1.Result.PSObject.Properties.Name | Sort-Object) 'the summary has exactly the required properties'
    Assert-Equal 2 $s1.Result.TokensAcquired 'the summary reports 2 tokens acquired'
    Assert-Equal 7 $s1.Result.AlertsFound    'the summary reports 7 alerts found'
    Assert-Equal 7 $s1.Result.AlertsActioned 'the summary reports 7 alerts actioned'
    Assert-Equal $expectedActive @($s1.Result.ActionedAlertIds | Sort-Object) 'the summary lists exactly the alerts it actioned'
    Assert-Equal $actionedIds @($s1.Result.ActionedAlertIds) 'the summary lists alert ids in action order'
}

# =====================================================================
Write-Host "`n== Scenario 2: optional fields are sent when they are set ==" -ForegroundColor Cyan

$expectedCriticalHosts = @($corpus |
    Where-Object { $_.alertLevel -eq 'CRITICAL' -and $_.resourceKind -eq 'HostSystem' } |
    ForEach-Object { $_.alertId } | Sort-Object)
$owner = '4c9e2b17-8a30-4d55-b6f1-0e7c3a9d2481'
$s2 = Invoke-Scenario -Fixture 'scenario-filtered.json' -Action 'takeOwnership' `
    -AuthSource 'CorpLDAP' -OwnerAccountId $owner -AlertCriticality @('CRITICAL') -ResourceKind 'HostSystem'

Assert-That ($s2.ExitCode -eq 0) "the filtered run completes without terminating (stderr: $($s2.Stderr)$($s2.MockErr))"
Assert-That (@($s2.All | Where-Object { $_.contractViolation }).Count -eq 0) 'no request falls outside the contract'

$acq2 = @($s2.Module | Where-Object { $_.operationId -eq 'acquireToken' })
Assert-Equal 1 $acq2.Count 'with no revocation the module acquires exactly one token'
if ($acq2.Count -ge 1) {
    Assert-Equal @('authSource', 'password', 'username') (Get-Keys $acq2[0]) `
        'when an auth source is supplied it appears in the acquireToken body -- omission is conditional, not hardcoded'
    Assert-That ((($acq2[0].body | ConvertFrom-Json).authSource) -eq 'CorpLDAP') 'the auth source sent is the one supplied'
    Assert-That ((($acq2[0].body | ConvertFrom-Json).username) -eq 'svc-triage') 'the filtered run uses the supplied credential username'
    Assert-That ((($acq2[0].body | ConvertFrom-Json).password) -eq 'triage-secret') 'the filtered run uses the supplied credential password'
    Assert-Equal @() (Get-QueryKeys $acq2[0]) 'the filtered acquireToken request sends no query parameters'
    Assert-That ($acq2[0].contentType -eq 'application/json') 'the filtered acquireToken request sends application/json'
}

$q2 = @($s2.Module | Where-Object { $_.operationId -eq 'queryAlert' })
Assert-Equal 1 $q2.Count 'the two filtered alerts fit in one page at the default page size'
foreach ($q in $q2) {
    Assert-Equal @('alertCriticality', 'resourceKind') (Get-Keys $q) `
        "queryAlert sends both filters that were set and omits the unset activeOnly (seq $($q.seq))"
    Assert-Equal @('CRITICAL') @(($q.body | ConvertFrom-Json).alertCriticality) 'the criticality filter sent is the one supplied'
    Assert-That ((($q.body | ConvertFrom-Json).resourceKind) -eq 'HostSystem') 'the resource kind filter sent is the one supplied'
    Assert-That ($q.query.pageSize -eq '100') "the effective default pageSize=100 is sent when the caller omits PageSize (seq $($q.seq))"
    Assert-Equal @('page', 'pageSize') (Get-QueryKeys $q) `
        "the filtered queryAlert request sends exactly page and pageSize (seq $($q.seq))"
    Assert-That ($q.contentType -eq 'application/json') "the filtered queryAlert request sends application/json (seq $($q.seq))"
}

$m2 = @($s2.Module | Where-Object { $_.operationId -eq 'modifyAlerts' -and $_.status -eq 200 })
Assert-Equal 2 $m2.Count 'exactly the 2 critical HostSystem alerts are actioned'
Assert-Equal $expectedCriticalHosts @($m2 | ForEach-Object { Get-ActionedId $_ } | Sort-Object) 'the action covers exactly the critical HostSystem alerts'
foreach ($m in $m2) {
    # Mirror of scenario 1: here userAccountID is set and minutes is not, so the
    # same code must now omit the other one.
    Assert-Equal @('action', 'userAccountID') (Get-QueryKeys $m) `
        "modifyAlerts sends exactly action and userAccountID; the unset minutes is absent (seq $($m.seq))"
    Assert-Equal @('uuids') (Get-Keys $m) "the filtered modifyAlerts body carries only uuids (seq $($m.seq))"
    Assert-Equal 1 @(Get-ActionedId $m).Count "the filtered modifyAlerts request acts on one alert (seq $($m.seq))"
    Assert-That ($m.contentType -eq 'application/json') "the filtered modifyAlerts request sends application/json (seq $($m.seq))"
    Assert-That ($m.query.action -eq 'takeOwnership') "the action sent is the one requested (seq $($m.seq))"
    Assert-That ($m.query.userAccountID -eq $owner)   "the owner sent is the one requested (seq $($m.seq))"
}
Assert-Equal 1 @($s2.Module | Where-Object { $_.operationId -eq 'releaseToken' }).Count 'the filtered run releases its token once'
if ($s2.Result) {
    Assert-Equal @('ActionedAlertIds', 'AlertsActioned', 'AlertsFound', 'TokensAcquired') `
        @($s2.Result.PSObject.Properties.Name | Sort-Object) 'the filtered summary has exactly the required properties'
    Assert-Equal 1 $s2.Result.TokensAcquired 'the filtered run reports one token acquired'
    Assert-Equal 2 $s2.Result.AlertsFound 'the filtered run reports 2 alerts found'
    Assert-Equal 2 $s2.Result.AlertsActioned 'the filtered run reports 2 alerts actioned'
    Assert-Equal $expectedCriticalHosts @($s2.Result.ActionedAlertIds | Sort-Object) `
        'the filtered summary lists exactly the alerts it actioned'
    Assert-Equal @($m2 | ForEach-Object { Get-ActionedId $_ }) @($s2.Result.ActionedAlertIds) `
        'the filtered summary lists alert ids in action order'
}

# =====================================================================
Write-Host ''
if ($script:Failures.Count -gt 0) {
    Write-Host ("{0} of {1} checks failed:" -f $script:Failures.Count, $script:Checks) -ForegroundColor Red
    $script:Failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
Write-Host ("All {0} checks passed." -f $script:Checks) -ForegroundColor Green
exit 0
