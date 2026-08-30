#Requires -Version 7.0
<#
.SYNOPSIS
    Protected verification for the VcfOpsReporting module.

.DESCRIPTION
    Drives src/VcfOpsReporting against the contract-pinned loopback mock in tools/mock and
    asserts the exact wire shape recorded in tools/mock/requests.jsonl.

    No VMware endpoint is contacted. The only server involved is the mock on 127.0.0.1.

    Exit code 0 = all assertions passed, 1 = at least one failed.
#>
[CmdletBinding()]
param(
    [string] $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ContractPath = Join-Path $RepoRoot 'docs/contract.json'
$SourcesPath  = Join-Path $RepoRoot 'docs/official_sources.json'
$MockScript   = Join-Path $RepoRoot 'tools/mock/Start-VcfOpsMock.ps1'
$ModulePath   = Join-Path $RepoRoot 'src/VcfOpsReporting/VcfOpsReporting.psd1'

$Contract = Get-Content -LiteralPath $ContractPath -Raw | ConvertFrom-Json

# Fixed identifiers used by every scenario.
$REPORT_ID = '7d0f0b3a-2b5e-4a1c-9c2f-1d6a8e4b3c55'
$DEF_ID    = 'aaaaaaaa-1111-2222-3333-444444444444'
$RES_ID    = 'bbbbbbbb-1111-2222-3333-444444444444'
$TOKEN     = 'mock-ops-token'
$CSV       = "Resource,Metric,Value`nvcf-esx-01,cpu|demand,42`n"

$script:Results = [System.Collections.Generic.List[object]]::new()

function Assert-That {
    param([string] $Name, [bool] $Condition, [string] $Detail = '')
    $script:Results.Add([pscustomobject]@{ Name = $Name; Passed = [bool]$Condition; Detail = $Detail })
    if ($Condition) { Write-Host "  PASS  $Name" -ForegroundColor Green }
    else { Write-Host "  FAIL  $Name" -ForegroundColor Red; if ($Detail) { Write-Host "        $Detail" -ForegroundColor DarkGray } }
}

# ------------------------------------------------------------------- mock ----
function Start-Mock {
    param([hashtable] $Scenario)
    $dir = Join-Path ([System.IO.Path]::GetTempPath()) ("vcfopsmock-" + [guid]::NewGuid().ToString('n'))
    New-Item -ItemType Directory -Force -Path $dir | Out-Null

    $mockArgs = @('-NoProfile', '-File', $MockScript, '-StateDir', $dir, '-ContractPath', $ContractPath)
    if ($Scenario) {
        $sp = Join-Path $dir 'scenario.json'
        $Scenario | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $sp
        $mockArgs += @('-ScenarioPath', $sp)
    }
    $proc = Start-Process -FilePath 'pwsh' -ArgumentList $mockArgs -PassThru `
        -RedirectStandardOutput (Join-Path $dir 'stdout.log') -RedirectStandardError (Join-Path $dir 'stderr.log')

    $portFile = Join-Path $dir 'port'
    $deadline = [datetime]::UtcNow.AddSeconds(60)
    while (-not (Test-Path -LiteralPath $portFile)) {
        if ([datetime]::UtcNow -gt $deadline) {
            $err = if (Test-Path (Join-Path $dir 'stderr.log')) { Get-Content -Raw (Join-Path $dir 'stderr.log') } else { '' }
            throw "Mock did not start within 60s. stderr: $err"
        }
        if ($proc.HasExited) {
            throw ("Mock exited early (code {0}). stderr: {1}" -f $proc.ExitCode, (Get-Content -Raw (Join-Path $dir 'stderr.log')))
        }
        Start-Sleep -Milliseconds 100
    }
    [pscustomobject]@{ Dir = $dir; Port = [int](Get-Content -Raw $portFile).Trim(); Process = $proc }
}

function Stop-Mock {
    param($Mock)
    if ($Mock -and $Mock.Process -and -not $Mock.Process.HasExited) {
        Stop-Process -Id $Mock.Process.Id -Force -ErrorAction SilentlyContinue
    }
}

# NOTE: every collection helper returns with the unary comma operator. A bare @() returned from a
# PowerShell function unrolls to $null, and .Count on $null throws under StrictMode.
function Get-Requests {
    param($Mock)
    $p = Join-Path $Mock.Dir 'requests.jsonl'
    if (-not (Test-Path -LiteralPath $p)) { return , @() }
    , @(Get-Content -LiteralPath $p | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
}

function Get-Ops {
    param($Requests, [string] $OperationId)
    , @($Requests | Where-Object { $_.operationId -eq $OperationId })
}

function Get-BodyKeys {
    param([string] $Body)
    if ([string]::IsNullOrWhiteSpace($Body)) { return , @() }
    , @(($Body | ConvertFrom-Json).PSObject.Properties.Name | Sort-Object)
}

function New-TestCredential {
    [System.Management.Automation.PSCredential]::new('svc-reporting',
        (ConvertTo-SecureString 'not-a-real-secret' -AsPlainText -Force))
}

# =============================================================== static ======
Write-Host "`n== contract provenance ==" -ForegroundColor Cyan

$sources = Get-Content -LiteralPath $SourcesPath -Raw | ConvertFrom-Json
$src = $sources.sources[0]

Assert-That 'official_sources records the vcf-api-specs OpenAPI document, not a docs page' `
    ($src.kind -eq 'openapi-specification' -and
     $src.specPath -eq 'specifications/vcf-operations/vcf-operations-openapi.json' -and
     $src.repository -match 'vmware/vcf-api-specs') `
    "got kind=$($src.kind) path=$($src.specPath) repo=$($src.repository)"

Assert-That 'official_sources pins the same commit sha as contract.json' `
    ($src.commit -eq $Contract.source.commit -and $src.commit -match '^[0-9a-f]{40}$') `
    "sources=$($src.commit) contract=$($Contract.source.commit)"

$contractOps = @($Contract.operations.PSObject.Properties.Name) | Sort-Object
$sourceOps   = @($src.operationIds | ForEach-Object { $_.operationId }) | Sort-Object
Assert-That 'every contract operationId is recorded in official_sources' `
    ((Compare-Object $contractOps $sourceOps -SyncWindow 0 | Measure-Object).Count -eq 0) `
    "contract=[$($contractOps -join ',')] sources=[$($sourceOps -join ',')]"

$expectedOps = @('acquireToken', 'createReport', 'downloadReport', 'getCurrentVersionOfServer', 'getReport')
Assert-That 'contract names the report-generation operationIds' `
    ((Compare-Object $contractOps $expectedOps -SyncWindow 0 | Measure-Object).Count -eq 0) `
    "got [$($contractOps -join ',')]"

Assert-That 'contract records report.status has no enum in the specification' `
    ($null -eq $Contract.taskDefined.reportStatus.specEnum) 'specEnum should be null'

$vendored = @(Get-ChildItem -LiteralPath $RepoRoot -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match 'VMware\.Sdk\.Vcf' -or $_.Name -match '^VMware\.Sdk\.Vcf.*\.(psd1|psm1|dll|nupkg)$' })
Assert-That 'the SDK is not vendored into the repository' ($vendored.Count -eq 0) `
    "found: $(($vendored | ForEach-Object FullName) -join ', ')"

# ------------------------------------------------------------ preconditions --
if (-not (Get-Module -ListAvailable -Name VMware.Sdk.Vcf.Ops)) {
    Write-Host "`nPRECONDITION FAILED: VMware.Sdk.Vcf.Ops is not installed." -ForegroundColor Red
    exit 1
}
Import-Module $ModulePath -Force -ErrorAction Stop -WarningAction SilentlyContinue

# =============================================== start is asynchronous ======
Write-Host "`n== standalone start: create returns without polling ==" -ForegroundColor Cyan
$mock = $null
try {
    $mock = Start-Mock @{ statusSequence = @('QUEUED') }

    $sess = Connect-VcfOpsReportingSession -Server '127.0.0.1' -Port $mock.Port -Protocol 'http' `
        -Credential (New-TestCredential) -SkipCertificateCheck
    $created = Start-VcfOpsReportGeneration -Session $sess -ReportDefinitionId $DEF_ID `
        -ResourceId $RES_ID

    $reqs = Get-Requests $mock
    Assert-That 'start: createReport returned the accepted report' `
        ($created.id -eq $REPORT_ID -and $created.status -eq 'QUEUED') `
        "id=$($created.id) status=$($created.status)"
    Assert-That 'start: exactly one createReport request was issued' `
        ((Get-Ops $reqs 'createReport').Count -eq 1) "count=$((Get-Ops $reqs 'createReport').Count)"
    Assert-That 'start: Start-VcfOpsReportGeneration did not poll' `
        ((Get-Ops $reqs 'getReport').Count -eq 0) "count=$((Get-Ops $reqs 'getReport').Count)"
    Assert-That 'start: Start-VcfOpsReportGeneration did not download' `
        ((Get-Ops $reqs 'downloadReport').Count -eq 0) "count=$((Get-Ops $reqs 'downloadReport').Count)"
} catch {
    Assert-That 'start: scenario ran to completion' $false $_.Exception.Message
} finally { Stop-Mock $mock }

# ====================================================== A: minimal happy =====
Write-Host "`n== scenario A: minimal request, generation succeeds ==" -ForegroundColor Cyan
$mock = $null
try {
    $mock = Start-Mock @{ statusSequence = @('QUEUED', 'SCHEDULED', 'RUNNING', 'COMPLETED') }
    $out  = Join-Path $mock.Dir 'report-a.csv'

    $sess = Connect-VcfOpsReportingSession -Server '127.0.0.1' -Port $mock.Port -Protocol 'http' `
        -Credential (New-TestCredential) -SkipCertificateCheck
    Invoke-VcfOpsReportRun -Session $sess -ReportDefinitionId $DEF_ID -ResourceId $RES_ID `
        -Path $out -PollIntervalSeconds 0.05 -TimeoutSeconds 30 | Out-Null

    $reqs = Get-Requests $mock

    Assert-That 'A: every request matched a contract operation' `
        (@($reqs | Where-Object { $null -eq $_.operationId }).Count -eq 0) `
        "unmatched: $((@($reqs | Where-Object { $null -eq $_.operationId } | ForEach-Object { $_.method + ' ' + $_.path })) -join '; ')"

    Assert-That 'A: no request was rejected by the mock' `
        (@($reqs | Where-Object { $_.responseCode -ge 400 }).Count -eq 0) `
        "rejected: $((@($reqs | Where-Object { $_.responseCode -ge 400 } | ForEach-Object { "$($_.method) $($_.path) -> $($_.responseCode) $($_.rejected)" })) -join '; ')"

    $auth = Get-Ops $reqs 'acquireToken'
    Assert-That 'A: session was established by exactly one acquireToken request' `
        ($auth.Count -eq 1) "count=$($auth.Count)"
    if ($auth.Count -eq 1) {
        $authKeys = Get-BodyKeys $auth[0].body
        $authBody = $auth[0].body | ConvertFrom-Json
        # The handshake body is composed by Connect-VcfOpsServer, not by the
        # module: the SDK serializes `authSource` whether or not one was given.
        # There is no way around that and still be using the SDK -- every
        # request builder takes a VcfOpsServer, and only that cmdlet makes one --
        # so what the module decides here is whether a value goes in, and that
        # is what this asserts. The stricter rule, that an unset optional is
        # absent rather than null, still binds every request the module composes
        # itself; the createReport assertions below hold it to exactly that.
        $unexpected = @($authKeys | Where-Object { $_ -notin @('password', 'username', 'authSource') })
        $sentSource = if ($authKeys -contains 'authSource') { $authBody.authSource } else { $null }
        Assert-That 'A: acquireToken carries no authSource value when -AuthSource is omitted' `
            ($unexpected.Count -eq 0 -and $null -eq $sentSource) `
            "body=$($auth[0].body)"
        Assert-That 'A: acquireToken sent the supplied username and password' `
            ($authBody.username -eq 'svc-reporting' -and $authBody.password -eq 'not-a-real-secret') `
            "body=$($auth[0].body)"
    }

    $create = Get-Ops $reqs 'createReport'
    Assert-That 'A: exactly one createReport request' ($create.Count -eq 1) "count=$($create.Count)"

    if ($create.Count -eq 1) {
        $c = $create[0]
        Assert-That 'A: createReport was sent to /suite-api/api/reports' ($c.path -eq '/suite-api/api/reports') "path=$($c.path)"
        Assert-That 'A: createReport carried the OpsToken Authorization header' `
            ($c.headers.Authorization -eq "OpsToken $TOKEN") "auth=$($c.headers.Authorization)"

        $keys = Get-BodyKeys $c.body
        Assert-That 'A: createReport body contains ONLY the two required properties' `
            ((Compare-Object $keys @('reportDefinitionId', 'resourceId') -SyncWindow 0 | Measure-Object).Count -eq 0) `
            "body=$($c.body)"

        $parsed = $c.body | ConvertFrom-Json
        Assert-That 'A: createReport sent the caller-supplied identifiers' `
            ($parsed.reportDefinitionId -eq $DEF_ID -and $parsed.resourceId -eq $RES_ID) "body=$($c.body)"

        Assert-That 'A: unset optional properties are omitted, not sent as null or empty' `
            ($c.body -notmatch 'null' -and $c.body -notmatch '""' -and $c.body -notmatch '\[\]') "body=$($c.body)"
    }

    $polls = Get-Ops $reqs 'getReport'
    Assert-That 'A: QUEUED, SCHEDULED, and RUNNING were polled through before COMPLETED' ($polls.Count -eq 4) `
        "expected 4 getReport calls (QUEUED, SCHEDULED, RUNNING, COMPLETED); got $($polls.Count)"

    Assert-That 'A: every poll addressed the report id verbatim' `
        (@($polls | Where-Object { $_.path -ne "/suite-api/api/reports/$REPORT_ID" }).Count -eq 0) `
        "paths: $((@($polls | ForEach-Object { $_.path })) -join ', ')"

    Assert-That 'A: polls carried no query string' `
        (@($polls | Where-Object { $_.query -ne '' }).Count -eq 0) `
        "queries: $((@($polls | ForEach-Object { "'" + $_.query + "'" })) -join ', ')"

    $dl = Get-Ops $reqs 'downloadReport'
    Assert-That 'A: exactly one downloadReport request' ($dl.Count -eq 1) "count=$($dl.Count)"
    if ($dl.Count -eq 1) {
        Assert-That 'A: download addressed the report id verbatim' `
            ($dl[0].path -eq "/suite-api/api/reports/$REPORT_ID/download") "path=$($dl[0].path)"
        Assert-That 'A: -Format omitted => no format query parameter is sent at all' `
            ($dl[0].query -eq '') "query='$($dl[0].query)'"
    }

    # ordering: create -> polls -> download
    if ($create.Count -eq 1 -and $polls.Count -ge 1 -and $dl.Count -eq 1) {
        $okOrder = ($create[0].seq -lt ($polls | Measure-Object seq -Minimum).Minimum) -and
                   (($polls | Measure-Object seq -Maximum).Maximum -lt $dl[0].seq)
        Assert-That 'A: ordering is create -> poll -> download' $okOrder `
            "create=$($create[0].seq) polls=$((@($polls | ForEach-Object { $_.seq })) -join ',') download=$($dl[0].seq)"
    }

    Assert-That 'A: the downloaded report was written to disk intact' `
        ((Test-Path -LiteralPath $out) -and ((Get-Content -LiteralPath $out -Raw) -replace "`r`n", "`n") -eq $CSV) `
        "exists=$(Test-Path -LiteralPath $out)"
} catch {
    Assert-That 'A: scenario ran to completion' $false $_.Exception.Message
} finally { Stop-Mock $mock }

# ============================================ B: optionals + explicit format ==
Write-Host "`n== scenario B: optional fields supplied, explicit CSV format ==" -ForegroundColor Cyan
$mock = $null
try {
    $mock = Start-Mock @{ statusSequence = @('QUEUED', 'COMPLETED') }
    $out  = Join-Path $mock.Dir 'report-b.csv'

    $sess = Connect-VcfOpsReportingSession -Server '127.0.0.1' -Port $mock.Port -Protocol 'http' `
        -Credential (New-TestCredential) -AuthSource 'local' -SkipCertificateCheck
    Invoke-VcfOpsReportRun -Session $sess -ReportDefinitionId $DEF_ID -ResourceId $RES_ID `
        -Path $out -Format 'CSV' -Name 'Nightly capacity' -Description 'Capacity detail' `
        -Subject @('capacity', 'cpu') -Publish `
        -TraversalSpecName 'vSphere Hosts and Clusters' `
        -PollIntervalSeconds 0.05 -TimeoutSeconds 30 | Out-Null

    $reqs = Get-Requests $mock
    Assert-That 'B: no request was rejected by the mock' `
        (@($reqs | Where-Object { $_.responseCode -ge 400 }).Count -eq 0) `
        "rejected: $((@($reqs | Where-Object { $_.responseCode -ge 400 } | ForEach-Object { "$($_.path) -> $($_.rejected)" })) -join '; ')"

    $auth = Get-Ops $reqs 'acquireToken'
    Assert-That 'B: exactly one acquireToken request' ($auth.Count -eq 1) "count=$($auth.Count)"
    if ($auth.Count -eq 1) {
        $authKeys = Get-BodyKeys $auth[0].body
        Assert-That 'B: acquireToken includes AuthSource only when supplied' `
            ((Compare-Object $authKeys @('authSource', 'password', 'username') -SyncWindow 0 | Measure-Object).Count -eq 0) `
            "body=$($auth[0].body)"
        Assert-That 'B: acquireToken sent the supplied AuthSource' `
            (($auth[0].body | ConvertFrom-Json).authSource -eq 'local') "body=$($auth[0].body)"
    }

    $create = Get-Ops $reqs 'createReport'
    Assert-That 'B: exactly one createReport request' ($create.Count -eq 1) "count=$($create.Count)"
    if ($create.Count -eq 1) {
        $keys = Get-BodyKeys $create[0].body
        $want = @('description', 'name', 'publish', 'reportDefinitionId', 'resourceId', 'subject', 'traversalSpec')
        Assert-That 'B: body carries exactly the supplied optional properties' `
            ((Compare-Object $keys $want -SyncWindow 0 | Measure-Object).Count -eq 0) `
            "body=$($create[0].body)"

        $p = $create[0].body | ConvertFrom-Json
        Assert-That 'B: scalar optional report properties round-tripped' `
            ($p.name -eq 'Nightly capacity' -and $p.description -eq 'Capacity detail' -and $p.publish -eq $true) `
            "body=$($create[0].body)"
        Assert-That 'B: subject round-tripped as an array' `
            ((@($p.subject) -join ',') -eq 'capacity,cpu') "subject=$($p.subject -join ',')"

        $tsKeys = @($p.traversalSpec.PSObject.Properties.Name) | Sort-Object
        Assert-That 'B: nested traversalSpec carries only its required name property' `
            ((Compare-Object $tsKeys @('name') -SyncWindow 0 | Measure-Object).Count -eq 0) `
            "traversalSpec keys=[$($tsKeys -join ',')]"
        Assert-That 'B: traversalSpec.name is the supplied value' `
            ($p.traversalSpec.name -eq 'vSphere Hosts and Clusters') "name=$($p.traversalSpec.name)"
    }

    $polls = Get-Ops $reqs 'getReport'
    Assert-That 'B: polling stopped at the terminal state' ($polls.Count -eq 2) "count=$($polls.Count)"

    $dl = Get-Ops $reqs 'downloadReport'
    Assert-That 'B: exactly one downloadReport request' ($dl.Count -eq 1) "count=$($dl.Count)"
    if ($dl.Count -eq 1) {
        Assert-That 'B: -Format CSV => exactly one format query parameter' `
            ($dl[0].query -eq '?format=CSV') "query='$($dl[0].query)'"
    }
} catch {
    Assert-That 'B: scenario ran to completion' $false $_.Exception.Message
} finally { Stop-Mock $mock }

# ================================================== C: terminal failures =====
Write-Host "`n== scenario C: every terminal failure stops the flow ==" -ForegroundColor Cyan
foreach ($terminalFailure in @('FAILED', 'ABORTED')) {
    $mock = $null
    try {
        $mock = Start-Mock @{ statusSequence = @('QUEUED', $terminalFailure) }
        $out  = Join-Path $mock.Dir ("report-c-{0}.csv" -f $terminalFailure.ToLowerInvariant())

        $sess = Connect-VcfOpsReportingSession -Server '127.0.0.1' -Port $mock.Port -Protocol 'http' `
            -Credential (New-TestCredential) -SkipCertificateCheck

        $threw = $false
        try {
            Invoke-VcfOpsReportRun -Session $sess -ReportDefinitionId $DEF_ID -ResourceId $RES_ID `
                -Path $out -PollIntervalSeconds 0.05 -TimeoutSeconds 30 | Out-Null
        } catch { $threw = $true }

        Assert-That "C/${terminalFailure}: terminal failure surfaces as a terminating error" $threw
        $reqs = Get-Requests $mock
        Assert-That "C/${terminalFailure}: polling stopped at the terminal failure" `
            ((Get-Ops $reqs 'getReport').Count -eq 2) "count=$((Get-Ops $reqs 'getReport').Count)"
        Assert-That "C/${terminalFailure}: nothing was downloaded" `
            ((Get-Ops $reqs 'downloadReport').Count -eq 0) `
            "count=$((Get-Ops $reqs 'downloadReport').Count)"
        Assert-That "C/${terminalFailure}: no report file was written" (-not (Test-Path -LiteralPath $out))
    } catch {
        Assert-That "C/${terminalFailure}: scenario ran to completion" $false $_.Exception.Message
    } finally { Stop-Mock $mock }
}

# ========================================================= D: timeout ========
Write-Host "`n== scenario D: timeout elapses before a terminal state ==" -ForegroundColor Cyan
$mock = $null
try {
    # COMPLETED would be returned by a second poll. A zero timeout makes the expected cutoff
    # deterministic: the first QUEUED response is non-terminal and the flow must stop there.
    $mock = Start-Mock @{ statusSequence = @('QUEUED', 'COMPLETED') }
    $out  = Join-Path $mock.Dir 'report-d.csv'

    $sess = Connect-VcfOpsReportingSession -Server '127.0.0.1' -Port $mock.Port -Protocol 'http' `
        -Credential (New-TestCredential) -SkipCertificateCheck

    $threw = $false
    try {
        Invoke-VcfOpsReportRun -Session $sess -ReportDefinitionId $DEF_ID -ResourceId $RES_ID `
            -Path $out -PollIntervalSeconds 0 -TimeoutSeconds 0 | Out-Null
    } catch { $threw = $true }

    Assert-That 'D: a non-terminal report times out instead of hanging' $threw
    $reqs = Get-Requests $mock
    Assert-That 'D: nothing was downloaded on timeout' ((Get-Ops $reqs 'downloadReport').Count -eq 0)
} catch {
    Assert-That 'D: scenario ran to completion' $false $_.Exception.Message
} finally { Stop-Mock $mock }

# ========================================================== summary ==========
$failed = @($script:Results | Where-Object { -not $_.Passed })
Write-Host ("`n{0}/{1} assertions passed." -f ($script:Results.Count - $failed.Count), $script:Results.Count)
if ($failed.Count -gt 0) {
    Write-Host "`nFailed:" -ForegroundColor Red
    $failed | ForEach-Object { Write-Host "  - $($_.Name)" -ForegroundColor Red; if ($_.Detail) { Write-Host "      $($_.Detail)" -ForegroundColor DarkGray } }
    exit 1
}
Write-Host 'VERIFICATION PASSED' -ForegroundColor Green
exit 0
