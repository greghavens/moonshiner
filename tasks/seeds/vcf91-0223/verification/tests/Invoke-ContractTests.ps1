#requires -Version 7.4
<#
    Acceptance test for VcfSddcLcm\Get-VcfSddcLcmComponentNode.

    Establishes a real PowerCLI session with Connect-VcfInstallerServer from
    VMware.Sdk.Vcf.Installer against a loopback session fixture, hands the
    resulting connection to the module under test, and asserts the module's
    behaviour plus the exact wire shape recorded by the contract-pinned mock.

    No VMware endpoint is contacted.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $WorkspaceRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# --------------------------------------------------------------------------
# assertion harness
# --------------------------------------------------------------------------

$script:Failures = [System.Collections.Generic.List[string]]::new()
$script:Checks = 0

function Fail([string] $Message) {
    $script:Failures.Add($Message) | Out-Null
    Write-Host ("  FAIL  {0}" -f $Message) -ForegroundColor Red
}

function Ok([string] $Message) {
    # Passing checks are counted by the caller, not printed: only failures need
    # to reach the console.
}

function Assert-True([bool] $Condition, [string] $Message) {
    $script:Checks++
    if (-not $Condition) { Fail $Message } else { Ok $Message }
}

function Assert-Equal($Expected, $Actual, [string] $Message) {
    $script:Checks++
    $e = if ($null -eq $Expected) { '<null>' } else { [string]$Expected }
    $a = if ($null -eq $Actual) { '<null>' } else { [string]$Actual }
    if ($e -cne $a) {
        Fail ("{0}`n          expected: {1}`n          actual:   {2}" -f $Message, $e, $a)
    } else {
        Ok $Message
    }
}

function Assert-SequenceEqual([string[]] $Expected, [string[]] $Actual, [string] $Message) {
    $script:Checks++
    $e = ($Expected -join ' | ')
    $a = ($Actual -join ' | ')
    if ($e -cne $a) {
        Fail ("{0}`n          expected: {1}`n          actual:   {2}" -f $Message, $e, $a)
    } else {
        Ok $Message
    }
}

# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

$testsDir = Join-Path $WorkspaceRoot 'tests'
$scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("vcf-sddc-lcm-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratch -Force | Out-Null

$runId = [guid]::NewGuid().ToString('N').Substring(0, 12)
$sessionToken = "vcf.session.$runId"
$env:VCF_FIXTURE_SESSION_TOKEN = $sessionToken
$env:VCF_FIXTURE_RUN_ID = $runId

$expectedNodeVersion = "9.1.0.0-$runId"
$expectedAuthorization = "Bearer $sessionToken"

$contractLog = Join-Path $scratch 'contract-requests.jsonl'
$sessionLog = Join-Path $scratch 'session-requests.jsonl'
$contractReady = Join-Path $scratch 'contract-ready.json'
$sessionReady = Join-Path $scratch 'session-ready.json'

$python = if ($IsWindows) { 'python' } else { 'python3' }
$processes = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

function Start-Fixture([string] $Script, [string] $ReadyFile, [string] $LogFile) {
    $p = Start-Process -FilePath $python `
        -ArgumentList @('-B', (Join-Path $testsDir $Script), $ReadyFile, $LogFile) `
        -PassThru -NoNewWindow
    $processes.Add($p) | Out-Null

    $deadline = [datetime]::UtcNow.AddSeconds(20)
    while ([datetime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath $ReadyFile) {
            return (Get-Content -LiteralPath $ReadyFile -Raw | ConvertFrom-Json)
        }
        if ($p.HasExited) {
            throw "Fixture $Script exited with code $($p.ExitCode) before becoming ready."
        }
        Start-Sleep -Milliseconds 50
    }
    throw "Fixture $Script did not become ready within 20 seconds."
}

$connection = $null

function Stop-Fixtures {
    foreach ($p in $processes) {
        try { if (-not $p.HasExited) { $p.Kill() } } catch { }
    }
}

# --------------------------------------------------------------------------
# request-log helpers
# --------------------------------------------------------------------------

$script:LastSeq = 0

function Get-NewContractRequests {
    if (-not (Test-Path -LiteralPath $contractLog)) { return @() }
    $entries = @(
        Get-Content -LiteralPath $contractLog |
            Where-Object { $_.Trim().Length -gt 0 } |
            ForEach-Object { $_ | ConvertFrom-Json } |
            Where-Object { $_.seq -gt $script:LastSeq } |
            Sort-Object seq
    )
    if ($entries.Count -gt 0) { $script:LastSeq = $entries[-1].seq }
    return $entries
}

function Assert-Targets([object[]] $Entries, [string[]] $Expected, [string] $Label) {
    Assert-SequenceEqual $Expected @($Entries | ForEach-Object { "$($_.method) $($_.target)" }) `
        "$Label -- exact request targets, in order"
}

function Assert-CommonWireShape([object[]] $Entries, [string] $Label) {
    foreach ($e in $Entries) {
        $tag = "$Label seq=$($e.seq) $($e.method) $($e.target)"

        Assert-True ($e.PSObject.Properties.Name -notcontains 'violation') `
            "$tag -- no contract violation recorded"
        Assert-Equal 'GET' $e.method "$tag -- method is GET"
        Assert-Equal 'application/json' $e.headers.accept "$tag -- Accept: application/json"
        Assert-Equal $expectedAuthorization $e.headers.authorization `
            "$tag -- Authorization carries the session secret from the VCF connection"
        Assert-Equal 0 $e.requestBodyBytes "$tag -- GET carries no request body"
        Assert-True ($null -eq $e.headers.contentType) "$tag -- no Content-Type request header"
        Assert-Equal '127.0.0.1' $e.clientAddress "$tag -- request arrived over loopback"

        foreach ($pair in $e.queryPairs) {
            $key = $pair[0]
            $value = if ($pair.Count -gt 1) { $pair[1] } else { $null }
            Assert-True ($null -ne $value -and $value -ne '') `
                "$tag -- query parameter '$key' is not sent empty"
        }
        Assert-True (-not $e.target.EndsWith('?')) "$tag -- no dangling '?' on the target"
    }
}

function Assert-NodeShape([object[]] $Nodes, [string] $Label) {
    foreach ($n in $Nodes) {
        $names = $n.PSObject.Properties.Name
        foreach ($p in @('id', 'name', 'nodeType', 'version', 'fqdn', 'ipAddress', 'status', 'size')) {
            Assert-True ($names -contains $p) "$Label -- node $($n.id) exposes '$p'"
        }
        Assert-Equal $expectedNodeVersion $n.version `
            "$Label -- node $($n.id) carries the per-run version stamped by the fixture"
    }
}

# --------------------------------------------------------------------------
# expectations
# --------------------------------------------------------------------------

$fleetOpsId = '11111111-1111-4111-8111-111111111111'
$instanceVcId = '22222222-2222-4222-8222-222222222222'
$missingId = '99999999-9999-4999-8999-999999999999'
$badMetadataId = '44444444-4444-4444-8444-444444444444'
$lateFailureId = '55555555-5555-4555-8555-555555555555'

# name ordinal ascending, then id ordinal ascending
$expectedAllNodes = @(
    'b1d2e3f4-0002-4a1b-9c2d-0000000000b1'  # ESX-02
    'e9051627-0005-4a1b-9c2d-0000000000e9'  # Esx-03
    '1b38495a-0008-4a1b-9c2d-0000000000b1'  # Worker
    'd2f40516-0004-4a1b-9c2d-0000000000d2'  # esx-01
    'a3c1e2d4-0001-4a1b-9c2d-0000000000a3'  # esx-04
    '0a273849-0007-4a1b-9c2d-0000000000a0'  # vcf-node
    'c7e3f405-0003-4a1b-9c2d-0000000000c7'  # worker
    'f4162738-0006-4a1b-9c2d-0000000000f4'  # worker
)

$expectedFilteredNodes = @(
    'b1d2e3f4-0002-4a1b-9c2d-0000000000b1'
    'e9051627-0005-4a1b-9c2d-0000000000e9'
    '1b38495a-0008-4a1b-9c2d-0000000000b1'
    'd2f40516-0004-4a1b-9c2d-0000000000d2'
    'a3c1e2d4-0001-4a1b-9c2d-0000000000a3'
    'c7e3f405-0003-4a1b-9c2d-0000000000c7'
    'f4162738-0006-4a1b-9c2d-0000000000f4'
)

# --------------------------------------------------------------------------

try {
    Write-Host 'Starting loopback fixtures' -ForegroundColor Cyan
    $session = Start-Fixture 'vcf_session_fixture.py' $sessionReady $sessionLog
    $contract = Start-Fixture 'sddc_lcm_contract_mock.py' $contractReady $contractLog

    # The children inherited these values when Start-Process launched them.
    # Remove the fixture-only secrets from the parent before loading candidate
    # code, so the bearer can only be obtained from the VCF connection object.
    Remove-Item Env:VCF_FIXTURE_SESSION_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:VCF_FIXTURE_RUN_ID -ErrorAction SilentlyContinue

    $serviceUri = [uri] $contract.baseUri
    Write-Host ("  session fixture 127.0.0.1:{0}   contract mock {1}" -f $session.port, $serviceUri)

    Write-Host 'Importing VMware.Sdk.Vcf.Installer' -ForegroundColor Cyan
    Import-Module VMware.Sdk.Vcf.Installer -WarningAction SilentlyContinue -ErrorAction Stop

    Write-Host 'Connecting with Connect-VcfInstallerServer' -ForegroundColor Cyan
    $password = ConvertTo-SecureString 'fixture-password' -AsPlainText -Force
    $connection = Connect-VcfInstallerServer -Server '127.0.0.1' -Port $session.port `
        -Protocol 'http' -User 'administrator@vcf.sddc.lab' -Password $password `
        -NotDefault -WarningAction SilentlyContinue -ErrorAction Stop

    Assert-Equal $sessionToken $connection.SessionSecret `
        'the PowerCLI connection holds the session secret minted by the fixture'

    Write-Host 'Importing the module under test' -ForegroundColor Cyan
    Import-Module (Join-Path $WorkspaceRoot 'VcfSddcLcm/VcfSddcLcm.psd1') `
        -Force -WarningAction SilentlyContinue -ErrorAction Stop

    $script:LastSeq = 0
    Get-NewContractRequests | Out-Null

    # ---------------------------------------------------------------- case 1
    Write-Host 'Case 1: by component id, service default page size' -ForegroundColor Cyan
    $nodes = @(Get-VcfSddcLcmComponentNode -Server $connection -ServiceUri $serviceUri `
            -ComponentId $fleetOpsId)
    $entries = Get-NewContractRequests

    Assert-Equal 8 $nodes.Count 'case 1 -- every node of every page is returned'
    Assert-SequenceEqual $expectedAllNodes @($nodes | ForEach-Object { $_.id }) `
        'case 1 -- nodes are ordered by name then id, ordinal ascending'
    Assert-NodeShape $nodes 'case 1'
    Assert-Targets $entries @(
        "GET /v1/components/$fleetOpsId/nodes?pageNumber=0"
        "GET /v1/components/$fleetOpsId/nodes?pageNumber=1"
        "GET /v1/components/$fleetOpsId/nodes?pageNumber=2"
    ) 'case 1'
    Assert-CommonWireShape $entries 'case 1'

    # ---------------------------------------------------------------- case 2
    Write-Host 'Case 2: by component type with scope and explicit page size' -ForegroundColor Cyan
    $nodes = @(Get-VcfSddcLcmComponentNode -Server $connection -ServiceUri $serviceUri `
            -ComponentType 'VCF_OPERATIONS' -Scope 'FLEET' -PageSize 2)
    $entries = Get-NewContractRequests

    Assert-Equal 8 $nodes.Count 'case 2 -- every node of every page is returned'
    Assert-SequenceEqual $expectedAllNodes @($nodes | ForEach-Object { $_.id }) `
        'case 2 -- ordering is independent of page size'
    Assert-Targets $entries @(
        'GET /v1/components?scope=FLEET'
        "GET /v1/components/$fleetOpsId/nodes?pageNumber=0&pageSize=2"
        "GET /v1/components/$fleetOpsId/nodes?pageNumber=1&pageSize=2"
        "GET /v1/components/$fleetOpsId/nodes?pageNumber=2&pageSize=2"
        "GET /v1/components/$fleetOpsId/nodes?pageNumber=3&pageSize=2"
    ) 'case 2'
    Assert-CommonWireShape $entries 'case 2'

    # ---------------------------------------------------------------- case 3
    Write-Host 'Case 3: ambiguous component type, scope omitted entirely' -ForegroundColor Cyan
    $threw = $false
    try {
        Get-VcfSddcLcmComponentNode -Server $connection -ServiceUri $serviceUri `
            -ComponentType 'VCF_OPERATIONS' | Out-Null
    } catch {
        $threw = $true
    }
    $entries = Get-NewContractRequests

    Assert-True $threw 'case 3 -- an ambiguous component type is a terminating error'
    Assert-Targets $entries @('GET /v1/components') 'case 3'
    Assert-CommonWireShape $entries 'case 3'

    # ---------------------------------------------------------------- case 4
    Write-Host 'Case 4: node type filter' -ForegroundColor Cyan
    $nodes = @(Get-VcfSddcLcmComponentNode -Server $connection -ServiceUri $serviceUri `
            -ComponentId $fleetOpsId -NodeType @('control-plane', 'worker'))
    $entries = Get-NewContractRequests

    Assert-Equal 7 $nodes.Count 'case 4 -- the filtered collection is complete'
    Assert-SequenceEqual $expectedFilteredNodes @($nodes | ForEach-Object { $_.id }) `
        'case 4 -- the filtered collection keeps the required order'
    Assert-Targets $entries @(
        "GET /v1/components/$fleetOpsId/nodes?pageNumber=0&nodeTypes=control-plane,worker"
        "GET /v1/components/$fleetOpsId/nodes?pageNumber=1&nodeTypes=control-plane,worker"
        "GET /v1/components/$fleetOpsId/nodes?pageNumber=2&nodeTypes=control-plane,worker"
    ) 'case 4'
    Assert-CommonWireShape $entries 'case 4'

    # ---------------------------------------------------------------- case 5
    Write-Host 'Case 5: filter that matches nothing' -ForegroundColor Cyan
    $nodes = @(Get-VcfSddcLcmComponentNode -Server $connection -ServiceUri $serviceUri `
            -ComponentId $fleetOpsId -NodeType @('absent-type') -PageSize 5)
    $entries = Get-NewContractRequests

    Assert-Equal 0 $nodes.Count 'case 5 -- an empty collection is not an error'
    Assert-Targets $entries @(
        "GET /v1/components/$fleetOpsId/nodes?pageNumber=0&pageSize=5&nodeTypes=absent-type"
    ) 'case 5'
    Assert-CommonWireShape $entries 'case 5'

    # ---------------------------------------------------------------- case 6
    Write-Host 'Case 6: unknown component id' -ForegroundColor Cyan
    $threw = $false
    try {
        Get-VcfSddcLcmComponentNode -Server $connection -ServiceUri $serviceUri `
            -ComponentId $missingId | Out-Null
    } catch {
        $threw = $true
    }
    $entries = Get-NewContractRequests

    Assert-True $threw 'case 6 -- a 404 from the service is a terminating error'
    Assert-Targets $entries @("GET /v1/components/$missingId/nodes?pageNumber=0") 'case 6'
    Assert-Equal 404 $entries[0].status 'case 6 -- the fixture answered 404'
    Assert-True ($entries[0].PSObject.Properties.Name -notcontains 'violation') `
        'case 6 -- the 404 is a contract response, not a contract violation'

    # ---------------------------------------------------------------- case 7
    Write-Host 'Case 7: unique component type without a scope filter' -ForegroundColor Cyan
    $nodes = @(Get-VcfSddcLcmComponentNode -Server $connection -ServiceUri $serviceUri `
            -ComponentType 'VCENTER')
    $entries = Get-NewContractRequests

    Assert-Equal 1 $nodes.Count 'case 7 -- the resolved component''s nodes are returned'
    Assert-Equal 'vc-01' $nodes[0].name 'case 7 -- the right component was resolved'
    Assert-Targets $entries @(
        'GET /v1/components'
        "GET /v1/components/$instanceVcId/nodes?pageNumber=0"
    ) 'case 7'
    Assert-CommonWireShape $entries 'case 7'

    # ---------------------------------------------------------------- case 8
    Write-Host 'Case 8: ordinal component type mismatch' -ForegroundColor Cyan
    $threw = $false
    try {
        Get-VcfSddcLcmComponentNode -Server $connection -ServiceUri $serviceUri `
            -ComponentType 'vcenter' | Out-Null
    } catch {
        $threw = $true
    }
    $entries = Get-NewContractRequests

    Assert-True $threw 'case 8 -- zero ordinal component matches is a terminating error'
    Assert-Targets $entries @('GET /v1/components') `
        'case 8 -- a failed resolution does not request nodes'
    Assert-CommonWireShape $entries 'case 8'

    # ---------------------------------------------------------------- case 9
    Write-Host 'Case 9: page metadata does not echo the requested page' -ForegroundColor Cyan
    $threw = $false
    $emitted = [System.Collections.Generic.List[object]]::new()
    try {
        Get-VcfSddcLcmComponentNode -Server $connection -ServiceUri $serviceUri `
            -ComponentId $badMetadataId |
            ForEach-Object { $emitted.Add($_) | Out-Null }
    } catch {
        $threw = $true
    }
    $entries = Get-NewContractRequests

    Assert-True $threw 'case 9 -- mismatched page metadata is a terminating error'
    Assert-Equal 0 $emitted.Count 'case 9 -- mismatched metadata emits no partial result'
    Assert-Targets $entries @(
        "GET /v1/components/$badMetadataId/nodes?pageNumber=0"
    ) 'case 9 -- mismatched metadata does not advance pagination'
    Assert-Equal 200 $entries[0].status 'case 9 -- the malformed metadata arrived in a 2xx response'
    Assert-CommonWireShape $entries 'case 9'

    # ---------------------------------------------------------------- case 10
    Write-Host 'Case 10: a later page returns a non-2xx response' -ForegroundColor Cyan
    $threw = $false
    $emitted = [System.Collections.Generic.List[object]]::new()
    try {
        Get-VcfSddcLcmComponentNode -Server $connection -ServiceUri $serviceUri `
            -ComponentId $lateFailureId -PageSize 1 |
            ForEach-Object { $emitted.Add($_) | Out-Null }
    } catch {
        $threw = $true
    }
    $entries = Get-NewContractRequests

    Assert-True $threw 'case 10 -- a non-2xx response on a later page is a terminating error'
    Assert-Equal 0 $emitted.Count 'case 10 -- a later HTTP failure emits no partial result'
    Assert-Targets $entries @(
        "GET /v1/components/$lateFailureId/nodes?pageNumber=0&pageSize=1"
        "GET /v1/components/$lateFailureId/nodes?pageNumber=1&pageSize=1"
    ) 'case 10 -- pagination stops on the failing page'
    Assert-SequenceEqual @('200', '500') @($entries | ForEach-Object { [string]$_.status }) `
        'case 10 -- the fixture returned one successful page and then HTTP 500'
    Assert-CommonWireShape $entries 'case 10'

    # -------------------------------------------------------------- global
    Write-Host 'Global assertions' -ForegroundColor Cyan
    $all = @(
        Get-Content -LiteralPath $contractLog |
            Where-Object { $_.Trim().Length -gt 0 } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
    Assert-Equal 0 @($all | Where-Object { $_.PSObject.Properties.Name -contains 'violation' }).Count `
        'no request violated the pinned contract'
    Assert-Equal 0 @($all | Where-Object { $_.status -eq 401 }).Count `
        'no request was rejected as unauthorized'
    Assert-Equal 0 @($all | Where-Object { $_.status -eq 400 }).Count `
        'no request was rejected as malformed'
    Assert-Equal 0 @($all | Where-Object { $_.rawQuery -match '(^|&)[^=&]+(&|$)' }).Count `
        'no query parameter was sent as a bare key'
    Assert-Equal 0 @($all | Where-Object { $_.rawQuery -match '=(&|$)' }).Count `
        'no query parameter was sent with an empty value'
    Assert-Equal 0 @($all | Where-Object { $_.rawQuery -match 'null' }).Count `
        'no query parameter was sent as the literal string null'
    Assert-Equal 0 @($all | Where-Object { $_.operationId -notin @('getComponents', 'getComponentNodes') }).Count `
        'every request mapped to an operationId named by the contract'

    $sessionEntries = @(
        Get-Content -LiteralPath $sessionLog |
            Where-Object { $_.Trim().Length -gt 0 } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
    Assert-True (@($sessionEntries | Where-Object {
                $_.method -eq 'POST' -and $_.target -eq '/v1/tokens' -and $_.userAgent -match 'PowerCLI'
            }).Count -ge 1) 'the PowerCLI SDK performed the session handshake'
    Assert-Equal 0 @($sessionEntries | Where-Object { $_.status -eq 404 }).Count `
        'the module under test issued no requests against the session endpoint'

} catch {
    Fail ("the test run aborted: {0}`n          {1}" -f $_.Exception.Message, $_.ScriptStackTrace)
} finally {
    if ($null -ne $connection) {
        try {
            Disconnect-VcfInstallerServer -Server $connection -Confirm:$false `
                -WarningAction SilentlyContinue -ErrorAction SilentlyContinue | Out-Null
        } catch { }
    }
    Stop-Fixtures
    Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ''
if ($script:Failures.Count -gt 0) {
    Write-Host ("FAILED: {0} of {1} checks" -f $script:Failures.Count, $script:Checks) -ForegroundColor Red
    exit 1
}
Write-Host ("PASSED: {0} checks" -f $script:Checks) -ForegroundColor Green
exit 0
