<#
.SYNOPSIS
    Protected verification for the VCF Automation change flow.

.DESCRIPTION
    Drives Invoke-VcfaCatalogItemChange against the contract-pinned loopback double in
    mock/Start-VcfaMock.ps1, then reads the double's request log and asserts the exact wire
    shape of every request, plus the accuracy of the returned report.

    Contacts no VMware endpoint and no network beyond 127.0.0.1. Requires no modules beyond
    the one under test - deliberately not Pester, so the result does not depend on what
    happens to be installed.

    Exit code 0 means every assertion passed.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$repoRoot     = Split-Path -Parent $PSScriptRoot
$modulePath   = Join-Path $repoRoot 'src/VcfAutomation.Change/VcfAutomation.Change.psd1'
$mockScript   = Join-Path $repoRoot 'mock/Start-VcfaMock.ps1'
$contractPath = Join-Path $repoRoot 'docs/contract.json'

# Identifiers seeded by the test double. Kept in one place so a drift shows up as one edit.
$PROJECT_ID     = 'a4c81f0e-6d52-4b19-9f7c-2e0b8d31c6aa'
$SANDBOX_ID     = '7b3d29c4-8e15-4f6a-b2d0-3c9a1e58f7b2'
$ITEM_SMALL_ID  = '1f9a5c72-3e64-4d81-b0af-58c7d2916e43'
$DEPLOYMENT_ID  = 'd51b8e37-9c02-4a6f-8b14-73fe5a90c2d1'
$DB_RESOURCE_ID = 'e2f47a10-5b83-4c9d-a06e-18f7c3b52d64'
$READY_RESOURCE_ID = '624a187c-9e35-41d0-84b6-2c8fa970e153'
$TOKEN          = 'eyJhbGciOiJIUzI1NiJ9.verifier-issued-token'
$STEP_ORDER     = @('ResolveProject', 'ResolveCatalogItem', 'RequestCatalogItem',
                    'ResolveResource', 'SubmitResourceAction')

# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------
$script:Failures = [System.Collections.Generic.List[string]]::new()
$script:Passed   = 0
$script:Scope    = ''

function Fail([string] $Message) {
    $script:Failures.Add(('[{0}] {1}' -f $script:Scope, $Message))
    Write-Host ('  FAIL  {0}' -f $Message) -ForegroundColor Red
}
function Pass([string] $Message) {
    $script:Passed++
    Write-Host ('  ok    {0}' -f $Message) -ForegroundColor DarkGray
}
function Assert-True([bool] $Condition, [string] $Message) {
    if ($Condition) { Pass $Message } else { Fail $Message }
}
function Assert-Equal($Expected, $Actual, [string] $Message) {
    if ($Expected -eq $Actual) { Pass $Message }
    else { Fail ('{0} -- expected [{1}], got [{2}]' -f $Message, $Expected, $Actual) }
}
function Assert-Match([string] $Pattern, $Actual, [string] $Message) {
    if ([string] $Actual -match $Pattern) { Pass $Message }
    else { Fail ('{0} -- [{1}] does not match /{2}/' -f $Message, $Actual, $Pattern) }
}
function Assert-KeySet([string[]] $Expected, [string[]] $Actual, [string] $Message) {
    $missing = @($Expected | Where-Object { $Actual -notcontains $_ })
    $extra   = @($Actual   | Where-Object { $Expected -notcontains $_ })
    if ($missing.Count -eq 0 -and $extra.Count -eq 0) { Pass $Message }
    else {
        $parts = @()
        if ($missing.Count) { $parts += 'missing: ' + ($missing -join ', ') }
        if ($extra.Count)   { $parts += 'unexpected: ' + ($extra -join ', ') }
        Fail ('{0} -- {1} (sent: {2})' -f $Message, ($parts -join '; '), (($Actual | Sort-Object) -join ', '))
    }
}

function Get-Prop($Object, [string] $Name) {
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($property) { return $property.Value }
    return $null
}

# Walks a parsed JSON body and returns the dotted paths of any null-valued member. The
# contract forbids sending an unsupplied optional field as null.
function Find-NullPath($Node, [string] $Prefix = '') {
    $found = @()
    if ($null -eq $Node) { return @($Prefix) }
    if ($Node -is [System.Collections.IEnumerable] -and $Node -isnot [string]) {
        $i = 0
        foreach ($element in $Node) {
            $found += Find-NullPath -Node $element -Prefix ('{0}[{1}]' -f $Prefix, $i)
            $i++
        }
        return $found
    }
    if ($Node -is [pscustomobject]) {
        foreach ($property in $Node.PSObject.Properties) {
            $path = if ($Prefix) { '{0}.{1}' -f $Prefix, $property.Name } else { $property.Name }
            $found += Find-NullPath -Node $property.Value -Prefix $path
        }
    }
    return $found
}

# ---------------------------------------------------------------------------
# Mock lifecycle
# ---------------------------------------------------------------------------
function Get-FreeLoopbackPort {
    $probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $probe.Start()
    $port = $probe.LocalEndpoint.Port
    $probe.Stop()
    $port
}

function Start-Mock {
    $port    = Get-FreeLoopbackPort
    $workDir = Join-Path ([System.IO.Path]::GetTempPath()) ("vcfa-mock-" + [guid]::NewGuid().ToString('n'))
    New-Item -ItemType Directory -Path $workDir -Force | Out-Null
    $logPath   = Join-Path $workDir 'requests.jsonl'
    $readyPath = Join-Path $workDir 'ready'
    $stderrLog = Join-Path $workDir 'mock.stderr'

    $process = Start-Process -FilePath (Get-Process -Id $PID).Path -PassThru -NoNewWindow `
        -RedirectStandardError $stderrLog `
        -ArgumentList @(
            '-NoLogo', '-NoProfile', '-File', $mockScript,
            '-Port', $port, '-ContractPath', $contractPath,
            '-LogPath', $logPath, '-ReadyPath', $readyPath
        )

    $deadline = [datetime]::UtcNow.AddSeconds(30)
    while (-not (Test-Path -LiteralPath $readyPath)) {
        if ($process.HasExited) {
            $err = if (Test-Path -LiteralPath $stderrLog) { Get-Content -LiteralPath $stderrLog -Raw } else { '' }
            throw "The test double exited before it was ready (code $($process.ExitCode)).`n$err"
        }
        if ([datetime]::UtcNow -gt $deadline) { throw "The test double did not become ready within 30s." }
        Start-Sleep -Milliseconds 100
    }

    [pscustomobject] @{
        Process = $process
        BaseUri = "http://127.0.0.1:$port"
        LogPath = $logPath
        WorkDir = $workDir
    }
}

function Stop-Mock($Mock) {
    if ($script:Failures.Count -gt 0) {
        Write-Host ('  request log: {0}' -f $Mock.LogPath) -ForegroundColor DarkYellow
    }
    if ($Mock.Process -and -not $Mock.Process.HasExited) {
        Stop-Process -Id $Mock.Process.Id -Force -ErrorAction SilentlyContinue
        $Mock.Process.WaitForExit(5000) | Out-Null
    }
}

function Read-RequestLog($Mock) {
    if (-not (Test-Path -LiteralPath $Mock.LogPath)) { return @() }
    @(Get-Content -LiteralPath $Mock.LogPath |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        ForEach-Object { $_ | ConvertFrom-Json })
}

# ---------------------------------------------------------------------------
# Shared assertions over a request-log entry
# ---------------------------------------------------------------------------
function Assert-Entry($Entry, [string] $OperationId, [string] $Method, [string] $Path, [string] $Label) {
    Assert-Equal $OperationId (Get-Prop $Entry 'operationId') "$Label matched contract operation $OperationId"
    Assert-Equal $Method      (Get-Prop $Entry 'method')      "$Label used $Method"
    Assert-Equal $Path        (Get-Prop $Entry 'path')        "$Label targeted $Path"
    Assert-Equal "Bearer $TOKEN" (Get-Prop (Get-Prop $Entry 'headers') 'authorization') `
        "$Label carried the bearer token"
}

function Get-QueryKeys($Entry) {
    $query = Get-Prop $Entry 'query'
    if ($null -eq $query) { return @() }
    @($query.PSObject.Properties.Name)
}

function Get-QueryValue($Entry, [string] $Name) {
    Get-Prop (Get-Prop $Entry 'query') $Name
}

function Get-ParsedBody($Entry) {
    $raw = [string] (Get-Prop $Entry 'bodyRaw')
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
    try { return $raw | ConvertFrom-Json } catch { return $null }
}

function Assert-JsonBodyHygiene($Entry, [string] $Label) {
    $headers = Get-Prop $Entry 'headers'
    Assert-Match '^application/json' (Get-Prop $headers 'contentType') "$Label declared Content-Type application/json"
    $body = Get-ParsedBody $Entry
    if ($null -eq $body) { Fail "$Label sent a parseable JSON object"; return $null }
    Pass "$Label sent a parseable JSON object"
    $nulls = @(Find-NullPath -Node $body)
    Assert-True ($nulls.Count -eq 0) ("$Label sent no null-valued members" +
        $(if ($nulls.Count) { ' (found: ' + ($nulls -join ', ') + ')' } else { '' }))
    $body
}

function Assert-Report($Report, [string] $ExpectedFailedStep, [hashtable] $ExpectedStepStatus, [string] $Label) {
    Assert-Equal 'Failed' (Get-Prop $Report 'Status') "$Label report Status is Failed"
    Assert-Equal $ExpectedFailedStep (Get-Prop $Report 'FailedStep') "$Label report FailedStep is $ExpectedFailedStep"

    $steps = @(Get-Prop $Report 'Steps')
    Assert-Equal 5 $steps.Count "$Label report has one entry per step"
    if ($steps.Count -ne 5) { return }

    Assert-True (@(Compare-Object -ReferenceObject $STEP_ORDER -DifferenceObject @($steps | ForEach-Object { Get-Prop $_ 'Name' }) -SyncWindow 0).Count -eq 0) `
        "$Label report lists the five steps in execution order"

    foreach ($step in $steps) {
        $name = [string] (Get-Prop $step 'Name')
        Assert-Equal $ExpectedStepStatus[$name] (Get-Prop $step 'Status') "$Label step $name status"
        Assert-True (-not [string]::IsNullOrWhiteSpace([string] (Get-Prop $step 'Detail'))) `
            "$Label step $name carries a Detail"
    }
}

# ===========================================================================
# Setup
# ===========================================================================
Write-Host ''
Write-Host 'VCF Automation change-flow verification' -ForegroundColor Cyan
Write-Host ('=' * 60)

foreach ($required in $modulePath, $mockScript, $contractPath) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Missing required file: $required" }
}

Import-Module -Name $modulePath -Force -ErrorAction Stop

$script:Scope = 'module surface'
Write-Host ''
Write-Host 'Module surface' -ForegroundColor Yellow
foreach ($name in 'Connect-VcfaOrgSession', 'Initialize-VcfaCatalogItemRequest',
                  'Initialize-VcfaResourceActionRequest', 'Invoke-VcfaCatalogItemChange') {
    Assert-True ([bool] (Get-Command -Name $name -Module 'VcfAutomation.Change' -ErrorAction SilentlyContinue)) `
        "$name is exported"
}

# ===========================================================================
# Body builders in isolation
# ===========================================================================
$script:Scope = 'body builders'
Write-Host ''
Write-Host 'Body builders drop unbound parameters' -ForegroundColor Yellow

$minimalBody = $null
try { $minimalBody = Initialize-VcfaCatalogItemRequest -DeploymentName 'x' -ProjectId 'p' }
catch { Fail "Initialize-VcfaCatalogItemRequest threw: $($_.Exception.Message)" }
if ($minimalBody) {
    Assert-True ($minimalBody -is [System.Collections.IDictionary]) 'CatalogItemRequest builder returns a dictionary'
    Assert-KeySet @('deploymentName', 'projectId') @($minimalBody.Keys) 'CatalogItemRequest with two bound parameters carries exactly two keys'
    Assert-True ((ConvertTo-Json -InputObject $minimalBody -Depth 10 -Compress) -notmatch 'null') `
        'CatalogItemRequest serialises without null'
}

$fullBody = $null
try {
    $fullBody = Initialize-VcfaCatalogItemRequest -DeploymentName 'x' -ProjectId 'p' `
                    -Inputs @{ a = 1 } -Version 'v2.0' -Reason 'r' -BulkRequestCount 3
} catch { Fail "Initialize-VcfaCatalogItemRequest threw with all parameters: $($_.Exception.Message)" }
if ($fullBody) {
    Assert-KeySet @('deploymentName', 'projectId', 'inputs', 'version', 'reason', 'bulkRequestCount') `
        @($fullBody.Keys) 'CatalogItemRequest with every parameter bound carries all six keys'
}

$actionBody = $null
try { $actionBody = Initialize-VcfaResourceActionRequest -ActionId 'a' -Inputs @{ cpuCount = 2 } }
catch { Fail "Initialize-VcfaResourceActionRequest threw: $($_.Exception.Message)" }
if ($actionBody) {
    Assert-KeySet @('actionId', 'inputs') @($actionBody.Keys) 'ResourceActionRequest omits an unbound reason'
}

# ===========================================================================
# Scenario 1 - minimal call: every optional field left unbound
# ===========================================================================
$script:Scope = 'scenario 1 (minimal)'
Write-Host ''
Write-Host 'Scenario 1 - minimal change, unset optionals must be omitted' -ForegroundColor Yellow

$mock = Start-Mock
try {
    $session = Connect-VcfaOrgSession -BaseUri $mock.BaseUri -AccessToken $TOKEN
    $report  = $null
    try {
        $report = Invoke-VcfaCatalogItemChange -Session $session `
            -ProjectName 'eng-platform' -CatalogItemName 'Ubuntu 24.04 Small' `
            -DeploymentName 'billing-db-02' -Inputs ([ordered] @{ size = 'small'; image = 'ubuntu-24.04' }) `
            -ResourceName 'db-node' -ActionId 'Cloud.vSphere.Machine.Resize' `
            -ActionInputs ([ordered] @{ cpuCount = 4; totalMemoryMB = 8192 })
        Pass 'a failing later step did not raise a terminating error'
    } catch {
        Fail "Invoke-VcfaCatalogItemChange threw instead of reporting: $($_.Exception.Message)"
    }

    $log = Read-RequestLog $mock
    Assert-Equal 5 $log.Count 'exactly five requests reached the appliance'
    Assert-True (@($log | Where-Object { $null -eq (Get-Prop $_ 'operationId') }).Count -eq 0) `
        'no request fell outside the contract'
    Assert-True (@($log | Where-Object { (Get-Prop $_ 'method') -notin @('GET', 'POST') }).Count -eq 0) `
        'no request used a method outside GET/POST'
    Assert-True (@($log | Where-Object { [int] (Get-Prop $_ 'statusCode') -in @(400, 501) }).Count -eq 0) `
        'no request was rejected as malformed or unknown'

    if ($log.Count -eq 5) {
        # --- step 1 ---------------------------------------------------------
        Assert-Entry $log[0] 'getAllProjects' 'GET' '/project-service/api/projects' 'project lookup'
        $keys = Get-QueryKeys $log[0]
        Assert-True ($keys -notcontains 'name') 'project lookup did not invent an undocumented name filter'
        Assert-KeySet @() @(@($keys) | Where-Object { $_ -notin @('excludeViewer', 'excludeSupervisor',
            'excludeNotSharedProjectsForMember', 'withAnyPermission', '$select', 'page', 'size',
            '$orderby', 'apiVersion') }) 'project lookup used only documented query parameters'

        # --- step 2 ---------------------------------------------------------
        Assert-Entry $log[1] 'getCatalogItems' 'GET' '/catalog/api/items' 'catalog lookup'
        Assert-Equal 'Ubuntu 24.04 Small' (Get-QueryValue $log[1] 'search') 'catalog lookup searched on the item name'
        Assert-Equal $PROJECT_ID (Get-QueryValue $log[1] 'projects') 'catalog lookup scoped to the resolved project id'
        Assert-KeySet @() @(@(Get-QueryKeys $log[1]) | Where-Object { $_ -notin @('page', 'size', 'sort',
            'search', 'projects', 'types', 'expandProjects', 'expand', '$top', '$skip', '$orderby') }) `
            'catalog lookup used only documented query parameters'

        # --- step 3 ---------------------------------------------------------
        Assert-Entry $log[2] 'requestCatalogItemInstances' 'POST' "/catalog/api/items/$ITEM_SMALL_ID/request" 'catalog request'
        Assert-Equal 0 (Get-QueryKeys $log[2]).Count 'catalog request sent no query string'
        $body = Assert-JsonBodyHygiene $log[2] 'catalog request'
        if ($body) {
            Assert-KeySet @('deploymentName', 'projectId', 'inputs') @($body.PSObject.Properties.Name) `
                'catalog request body carries only the three supplied fields'
            Assert-Equal 'billing-db-02' (Get-Prop $body 'deploymentName') 'catalog request deploymentName'
            Assert-Equal $PROJECT_ID     (Get-Prop $body 'projectId')      'catalog request projectId'
            $inputs = Get-Prop $body 'inputs'
            Assert-Equal 'small'          (Get-Prop $inputs 'size')  'catalog request inputs.size'
            Assert-Equal 'ubuntu-24.04'   (Get-Prop $inputs 'image') 'catalog request inputs.image'
            foreach ($omitted in 'version', 'reason', 'bulkRequestCount') {
                Assert-True ($body.PSObject.Properties.Name -notcontains $omitted) `
                    "catalog request omitted the unset optional field '$omitted'"
            }
        }

        # --- step 4 ---------------------------------------------------------
        Assert-Entry $log[3] 'getDeploymentResources' 'GET' "/deployment/api/deployments/$DEPLOYMENT_ID/resources" 'resource lookup'
        Assert-Equal 'db-node' (Get-QueryValue $log[3] 'names') 'resource lookup used the documented exact-name filter'
        Assert-KeySet @() @(@(Get-QueryKeys $log[3]) | Where-Object { $_ -notin @('page', 'size', 'sort',
            'names', 'resourceTypes', 'tags', 'expand', '$top', '$skip', '$orderby', '$filter') }) `
            'resource lookup used only documented query parameters'

        # --- step 5 ---------------------------------------------------------
        Assert-Entry $log[4] 'submitResourceActionRequest' 'POST' `
            "/deployment/api/deployments/$DEPLOYMENT_ID/resources/$DB_RESOURCE_ID/requests" 'day-2 action'
        $actionWire = Assert-JsonBodyHygiene $log[4] 'day-2 action'
        if ($actionWire) {
            Assert-KeySet @('actionId', 'inputs') @($actionWire.PSObject.Properties.Name) `
                'day-2 action body carries only the two supplied fields'
            Assert-Equal 'Cloud.vSphere.Machine.Resize' (Get-Prop $actionWire 'actionId') 'day-2 action actionId'
            $actionInputs = Get-Prop $actionWire 'inputs'
            Assert-Equal 4    (Get-Prop $actionInputs 'cpuCount')      'day-2 action inputs.cpuCount'
            Assert-Equal 8192 (Get-Prop $actionInputs 'totalMemoryMB') 'day-2 action inputs.totalMemoryMB'
            Assert-True ($actionWire.PSObject.Properties.Name -notcontains 'reason') `
                "day-2 action omitted the unset optional field 'reason'"
        }
        Assert-Equal 409 ([int] (Get-Prop $log[4] 'statusCode')) 'the appliance rejected the day-2 action with 409'
    }

    # --- the report -------------------------------------------------------
    Assert-Report $report 'SubmitResourceAction' @{
        ResolveProject      = 'Succeeded'
        ResolveCatalogItem  = 'Succeeded'
        RequestCatalogItem  = 'Succeeded'
        ResolveResource     = 'Succeeded'
        SubmitResourceAction = 'Failed'
    } 'minimal'

    Assert-Equal $PROJECT_ID     (Get-Prop $report 'ProjectId')      'minimal report kept the resolved ProjectId'
    Assert-Equal $ITEM_SMALL_ID  (Get-Prop $report 'CatalogItemId')  'minimal report resolved the exact catalog item, not the retired near-match'
    Assert-Equal $DEPLOYMENT_ID  (Get-Prop $report 'DeploymentId')   'minimal report still reports the deployment created before the failure'
    Assert-Equal 'billing-db-02' (Get-Prop $report 'DeploymentName') 'minimal report kept the DeploymentName'
    Assert-Equal $DB_RESOURCE_ID (Get-Prop $report 'ResourceId')     'minimal report kept the resolved ResourceId'

    $failedStep = @(Get-Prop $report 'Steps') | Where-Object { (Get-Prop $_ 'Name') -eq 'SubmitResourceAction' }
    Assert-Match '409' (Get-Prop $failedStep 'Detail') 'the failed step reports the HTTP status code'
    Assert-Match 'in progress' (Get-Prop $failedStep 'Detail') 'the failed step relays the message the appliance returned'
}
finally { Stop-Mock $mock }

# ===========================================================================
# Scenario 2 - every optional field supplied
# ===========================================================================
$script:Scope = 'scenario 2 (all optionals)'
Write-Host ''
Write-Host 'Scenario 2 - supplied optionals must be sent' -ForegroundColor Yellow

$mock = Start-Mock
try {
    $session = Connect-VcfaOrgSession -BaseUri $mock.BaseUri -AccessToken $TOKEN
    $report  = $null
    try {
        $report = Invoke-VcfaCatalogItemChange -Session $session `
            -ProjectName 'eng-platform' -CatalogItemName 'Ubuntu 24.04 Small' `
            -DeploymentName 'billing-db-03' -Inputs ([ordered] @{ size = 'small' }) `
            -ResourceName 'db-node' -ActionId 'Cloud.vSphere.Machine.Resize' `
            -ActionInputs ([ordered] @{ cpuCount = 8 }) `
            -Reason 'CHG-4821 approved' -CatalogItemVersion 'v2.0' -BulkRequestCount 1 `
            -ActionReason 'CHG-4821 resize step'
        Pass 'a failing later step did not raise a terminating error'
    } catch {
        Fail "Invoke-VcfaCatalogItemChange threw instead of reporting: $($_.Exception.Message)"
    }

    $log = Read-RequestLog $mock
    Assert-Equal 5 $log.Count 'exactly five requests reached the appliance'
    Assert-True (@($log | Where-Object { [int] (Get-Prop $_ 'statusCode') -in @(400, 501) }).Count -eq 0) `
        'no request was rejected as malformed or unknown'

    if ($log.Count -eq 5) {
        $body = Assert-JsonBodyHygiene $log[2] 'catalog request'
        if ($body) {
            Assert-KeySet @('deploymentName', 'projectId', 'inputs', 'version', 'reason', 'bulkRequestCount') `
                @($body.PSObject.Properties.Name) 'catalog request body carries every supplied field'
            Assert-Equal 'v2.0'              (Get-Prop $body 'version') 'catalog request version'
            Assert-Equal 'CHG-4821 approved' (Get-Prop $body 'reason')  'catalog request reason'
            $bulk = Get-Prop $body 'bulkRequestCount'
            Assert-Equal 1 $bulk 'catalog request bulkRequestCount'
            Assert-True ($bulk -isnot [string]) 'catalog request sent bulkRequestCount as a JSON number'
        }

        $actionWire = Assert-JsonBodyHygiene $log[4] 'day-2 action'
        if ($actionWire) {
            Assert-KeySet @('actionId', 'inputs', 'reason') @($actionWire.PSObject.Properties.Name) `
                'day-2 action body carries every supplied field'
            Assert-Equal 'CHG-4821 resize step' (Get-Prop $actionWire 'reason') 'day-2 action reason'
        }
    }

    Assert-Report $report 'SubmitResourceAction' @{
        ResolveProject      = 'Succeeded'
        ResolveCatalogItem  = 'Succeeded'
        RequestCatalogItem  = 'Succeeded'
        ResolveResource     = 'Succeeded'
        SubmitResourceAction = 'Failed'
    } 'all-optionals'
    Assert-Equal 'billing-db-03' (Get-Prop $report 'DeploymentName') 'all-optionals report kept the DeploymentName'
    Assert-Equal $DEPLOYMENT_ID  (Get-Prop $report 'DeploymentId')   'all-optionals report kept the DeploymentId'
}
finally { Stop-Mock $mock }

# ===========================================================================
# Scenario 3 - the first step fails, so nothing later may run or be claimed
# ===========================================================================
$script:Scope = 'scenario 3 (first step fails)'
Write-Host ''
Write-Host 'Scenario 3 - failure on the first step stops the flow' -ForegroundColor Yellow

$mock = Start-Mock
try {
    $session = Connect-VcfaOrgSession -BaseUri $mock.BaseUri -AccessToken $TOKEN
    $report  = $null
    try {
        $report = Invoke-VcfaCatalogItemChange -Session $session `
            -ProjectName 'no-such-project' -CatalogItemName 'Ubuntu 24.04 Small' `
            -DeploymentName 'billing-db-04' -Inputs ([ordered] @{ size = 'small' }) `
            -ResourceName 'db-node' -ActionId 'Cloud.vSphere.Machine.Resize'
        Pass 'an unresolvable project did not raise a terminating error'
    } catch {
        Fail "Invoke-VcfaCatalogItemChange threw instead of reporting: $($_.Exception.Message)"
    }

    $log = Read-RequestLog $mock
    Assert-Equal 2 $log.Count 'the failed lookup read every project page and then stopped'
    if ($log.Count -ge 2) {
        Assert-Entry $log[0] 'getAllProjects' 'GET' '/project-service/api/projects' 'project lookup'
        Assert-Entry $log[1] 'getAllProjects' 'GET' '/project-service/api/projects' 'second project page'
        Assert-Equal '1' (Get-QueryValue $log[1] 'page') 'project lookup advanced to page 1'
    }
    Assert-True (@($log | Where-Object { (Get-Prop $_ 'method') -eq 'POST' }).Count -eq 0) `
        'nothing was created after the failed lookup'

    Assert-Report $report 'ResolveProject' @{
        ResolveProject      = 'Failed'
        ResolveCatalogItem  = 'Skipped'
        RequestCatalogItem  = 'Skipped'
        ResolveResource     = 'Skipped'
        SubmitResourceAction = 'Skipped'
    } 'unresolvable-project'

    foreach ($name in 'ProjectId', 'CatalogItemId', 'DeploymentId', 'DeploymentName', 'ResourceId') {
        Assert-True ([string]::IsNullOrEmpty([string] (Get-Prop $report $name))) `
            "report claims no $name when nothing got that far"
    }
    $failedStep = @(Get-Prop $report 'Steps') | Where-Object { (Get-Prop $_ 'Name') -eq 'ResolveProject' }
    Assert-Match 'no-such-project' (Get-Prop $failedStep 'Detail') 'the failed lookup says what it was looking for'
}
finally { Stop-Mock $mock }

# ===========================================================================
# Scenario 4 - the wanted project is on the second page
# ===========================================================================
$script:Scope = 'scenario 4 (paged project lookup)'
Write-Host ''
Write-Host 'Scenario 4 - project lookup follows pagination' -ForegroundColor Yellow

$mock = Start-Mock
try {
    $session = Connect-VcfaOrgSession -BaseUri $mock.BaseUri -AccessToken $TOKEN
    $report  = $null
    try {
        $report = Invoke-VcfaCatalogItemChange -Session $session `
            -ProjectName 'sandbox' -CatalogItemName 'Ubuntu 24.04 Small' `
            -DeploymentName 'sandbox-db-01' -Inputs ([ordered] @{ size = 'small' }) `
            -ResourceName 'db-node' -ActionId 'Cloud.vSphere.Machine.Resize'
        Pass 'a catalog miss after a paged project lookup did not raise a terminating error'
    } catch {
        Fail "Invoke-VcfaCatalogItemChange threw instead of reporting: $($_.Exception.Message)"
    }

    $log = Read-RequestLog $mock
    Assert-Equal 3 $log.Count 'two project pages and one catalog lookup reached the appliance'
    if ($log.Count -eq 3) {
        Assert-Entry $log[0] 'getAllProjects' 'GET' '/project-service/api/projects' 'first project page'
        Assert-Entry $log[1] 'getAllProjects' 'GET' '/project-service/api/projects' 'second project page'
        Assert-Equal '1' (Get-QueryValue $log[1] 'page') 'second project request selected page 1'
        Assert-Entry $log[2] 'getCatalogItems' 'GET' '/catalog/api/items' 'catalog lookup'
        Assert-Equal $SANDBOX_ID (Get-QueryValue $log[2] 'projects') `
            'catalog lookup used the project found on the second page'
    }
    Assert-True (@($log | Where-Object { (Get-Prop $_ 'method') -eq 'POST' }).Count -eq 0) `
        'nothing was created when the catalog item was not shared with the paged project'

    Assert-Report $report 'ResolveCatalogItem' @{
        ResolveProject       = 'Succeeded'
        ResolveCatalogItem   = 'Failed'
        RequestCatalogItem   = 'Skipped'
        ResolveResource      = 'Skipped'
        SubmitResourceAction = 'Skipped'
    } 'paged-project'
    Assert-Equal $SANDBOX_ID (Get-Prop $report 'ProjectId') 'paged-project report kept the resolved ProjectId'
    foreach ($name in 'CatalogItemId', 'DeploymentId', 'DeploymentName', 'ResourceId') {
        Assert-True ([string]::IsNullOrEmpty([string] (Get-Prop $report $name))) `
            "paged-project report claims no $name before that step succeeded"
    }
}
finally { Stop-Mock $mock }

# ===========================================================================
# Scenario 5 - all five operations succeed
# ===========================================================================
$script:Scope = 'scenario 5 (success)'
Write-Host ''
Write-Host 'Scenario 5 - a completed change reports success' -ForegroundColor Yellow

$mock = Start-Mock
try {
    $session = Connect-VcfaOrgSession -BaseUri $mock.BaseUri -AccessToken $TOKEN
    $report  = $null
    try {
        $report = Invoke-VcfaCatalogItemChange -Session $session `
            -ProjectName 'eng-platform' -CatalogItemName 'Ubuntu 24.04 Small' `
            -DeploymentName 'billing-ready-01' -Inputs ([ordered] @{ size = 'small' }) `
            -ResourceName 'ready-node' -ActionId 'Cloud.vSphere.Machine.PowerOn'
        Pass 'a successful change returned without raising a terminating error'
    } catch {
        Fail "Invoke-VcfaCatalogItemChange threw on the success path: $($_.Exception.Message)"
    }

    $log = Read-RequestLog $mock
    Assert-Equal 5 $log.Count 'the successful flow made exactly its five operation calls'
    if ($log.Count -eq 5) {
        Assert-Entry $log[4] 'submitResourceActionRequest' 'POST' `
            "/deployment/api/deployments/$DEPLOYMENT_ID/resources/$READY_RESOURCE_ID/requests" `
            'successful day-2 action'
        Assert-Equal 200 ([int] (Get-Prop $log[4] 'statusCode')) 'the successful day-2 action returned HTTP 200'
    }

    Assert-Equal 'Succeeded' (Get-Prop $report 'Status') 'success report Status is Succeeded'
    Assert-True ($null -eq (Get-Prop $report 'FailedStep')) 'success report has no FailedStep'
    $steps = @(Get-Prop $report 'Steps')
    Assert-Equal 5 $steps.Count 'success report has one entry per step'
    if ($steps.Count -eq 5) {
        Assert-True (@(Compare-Object -ReferenceObject $STEP_ORDER -DifferenceObject @($steps | ForEach-Object { Get-Prop $_ 'Name' }) -SyncWindow 0).Count -eq 0) `
            'success report lists the five steps in execution order'
        foreach ($step in $steps) {
            Assert-Equal 'Succeeded' (Get-Prop $step 'Status') "success step $((Get-Prop $step 'Name')) status"
            Assert-True (-not [string]::IsNullOrWhiteSpace([string] (Get-Prop $step 'Detail'))) `
                "success step $((Get-Prop $step 'Name')) carries a Detail"
        }
    }
    Assert-Equal $PROJECT_ID       (Get-Prop $report 'ProjectId')     'success report kept ProjectId'
    Assert-Equal $ITEM_SMALL_ID    (Get-Prop $report 'CatalogItemId') 'success report kept CatalogItemId'
    Assert-Equal $DEPLOYMENT_ID    (Get-Prop $report 'DeploymentId')  'success report kept DeploymentId'
    Assert-Equal 'billing-ready-01' (Get-Prop $report 'DeploymentName') 'success report kept DeploymentName'
    Assert-Equal $READY_RESOURCE_ID (Get-Prop $report 'ResourceId')   'success report kept ResourceId'
}
finally { Stop-Mock $mock }

# ===========================================================================
# Summary
# ===========================================================================
Write-Host ''
Write-Host ('=' * 60)
if ($script:Failures.Count -eq 0) {
    Write-Host ("PASS - {0} assertions" -f $script:Passed) -ForegroundColor Green
    exit 0
}
Write-Host ("FAIL - {0} of {1} assertions failed" -f $script:Failures.Count, ($script:Passed + $script:Failures.Count)) -ForegroundColor Red
foreach ($failure in $script:Failures) { Write-Host ("  - {0}" -f $failure) -ForegroundColor Red }
exit 1
