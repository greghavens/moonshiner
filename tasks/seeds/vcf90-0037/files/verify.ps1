# Protected acceptance harness for VcfResourcePoolRollout.psm1.
#
# Every request is made by the genuine VMware PowerCLI SDK against a
# contract-pinned loopback vCenter on 127.0.0.1 with dummy credentials. No live
# VMware endpoint is contacted. The mock's request log is the evidence: it
# carries the exact method, target, query keys, headers and body of everything
# the solution put on the wire.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture =
    [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture =
    [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'
# The PowerCLI SDK greets every import with a CEIP notice; it is not a result.
$WarningPreference = 'SilentlyContinue'
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

function Assert-Eq {
    param([string] $Label, $Expected, $Actual)
    $script:Checks++
    if ("$Expected" -ceq "$Actual") { return }
    $script:Failures++
    Write-Output "FAIL $Label"
    Write-Output "  expected: $Expected"
    Write-Output "  actual:   $Actual"
}

function Get-MemberOrNull {
    param([Parameter(Mandatory)] [AllowNull()] [object] $InputObject, [string] $Name)
    if ($null -eq $InputObject) { return $null }
    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    $property.Value
}

# A stable rendering of a parsed JSON node, so a body can be compared by shape
# and value rather than by whitespace or property order.
function ConvertTo-Canonical {
    param([Parameter(Mandatory)] [AllowNull()] [object] $Node)
    if ($null -eq $Node) { return 'null' }
    if ($Node -is [bool]) { if ($Node) { return 'true' } else { return 'false' } }
    if ($Node -is [string]) { return '"' + $Node + '"' }
    if ($Node -is [System.Management.Automation.PSCustomObject]) {
        $parts = foreach ($property in ($Node.PSObject.Properties | Sort-Object Name)) {
            '"' + $property.Name + '":' + (ConvertTo-Canonical -Node $property.Value)
        }
        return '{' + ($parts -join ',') + '}'
    }
    if ($Node -is [System.Collections.IEnumerable]) {
        $parts = foreach ($item in $Node) { ConvertTo-Canonical -Node $item }
        return '[' + ($parts -join ',') + ']'
    }
    [string] $Node
}

function ConvertTo-CanonicalJson {
    param([Parameter(Mandatory)] [string] $Json)
    ConvertTo-Canonical -Node ($Json | ConvertFrom-Json)
}

function Get-RequestLog {
    param([Parameter(Mandatory)] [string] $Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return , @() }
    , @(
        Get-Content -LiteralPath $Path |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
}

function Format-Trace {
    param([Parameter(Mandatory)] [AllowNull()] [object] $Log)
    (@($Log) | ForEach-Object { "$($_.method) $($_.path) $($_.status)" }) -join ' | '
}

function Get-QueryKeys {
    param([Parameter(Mandatory)] [object] $Record)
    (@($Record.queryKeys) | Sort-Object) -join ','
}

# ---------------------------------------------------------------------------
# The solution has to exist, and it has to be the only thing under test.
# ---------------------------------------------------------------------------

$modulePath = Join-Path $PSScriptRoot 'VcfResourcePoolRollout.psm1'
if (-not (Test-Path -LiteralPath $modulePath -PathType Leaf)) {
    Write-Output 'FAIL VcfResourcePoolRollout.psm1 not found in workspace root'
    exit 1
}

# The PowerCLI SDK is an environment prerequisite, never a fixture of this seed.
foreach ($prerequisite in @(
    @{ Name = 'VMware.Sdk.vSphere'; Version = '13.5.0.25380678' },
    @{ Name = 'VMware.Sdk.vSphereRuntime'; Version = '8.0.2099.24145081' }
)) {
    $installed = Get-Module -ListAvailable -Name $prerequisite.Name |
        Where-Object { $_.Version -ge [version] $prerequisite.Version } |
        Sort-Object Version -Descending |
        Select-Object -First 1
    if ($null -eq $installed) {
        Write-Output "FAIL prerequisite module $($prerequisite.Name) $($prerequisite.Version) is not installed"
        exit 1
    }
}

$vendored = @(
    Get-ChildItem -LiteralPath $PSScriptRoot -Directory -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like 'VMware.*' }
)
Assert-Eq 'no VMware SDK module is vendored into the workspace' '' `
    (($vendored | ForEach-Object FullName) -join ',')

$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $modulePath, [ref] $tokens, [ref] $parseErrors)
Assert-Eq 'solution is syntactically valid PowerShell' '' `
    ((@($parseErrors) | ForEach-Object Message) -join ',')
$commandNames = @(
    $ast.FindAll(
        { param($node) $node -is [System.Management.Automation.Language.CommandAst] },
        $true) |
        ForEach-Object { $_.GetCommandName() } |
        Where-Object { $null -ne $_ }
)
$typeNames = @(
    $ast.FindAll(
        { param($node) $node -is [System.Management.Automation.Language.TypeExpressionAst] },
        $true) |
        ForEach-Object { $_.TypeName.FullName }
)
$functionNames = @(
    $ast.FindAll(
        { param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] },
        $true) |
        ForEach-Object { $_.Name }
)

Assert-True 'solution issues requests through the SDK transport' (
    $commandNames -contains 'Invoke-vSphereApiClient' -and
    $commandNames -contains 'New-vSphereServerConfiguration'
)
foreach ($bannedCommand in @(
    'Invoke-RestMethod', 'irm', 'Invoke-WebRequest', 'iwr', 'curl', 'curl.exe',
    'wget', 'Start-BitsTransfer'
)) {
    Assert-True "solution does not replace the SDK transport with $bannedCommand" (
        $commandNames -notcontains $bannedCommand
    )
}
foreach ($typeName in $typeNames) {
    Assert-True "solution does not replace the SDK transport with $typeName" (
        $typeName -notmatch '(?i)(^|\.)(HttpClient|HttpWebRequest|WebRequest|WebClient|TcpClient|UdpClient|Socket)$'
    )
}
foreach ($modelInitializer in @(
    'Initialize-VcenterResourcePoolCreateSpec',
    'Initialize-VcenterResourcePoolResourceAllocationCreateSpec',
    'Initialize-VcenterResourcePoolSharesInfo'
)) {
    Assert-True "solution builds request bodies with $modelInitializer" (
        $commandNames -contains $modelInitializer
    )
}
foreach ($sdkCommand in @(
    'New-vSphereServerConfiguration',
    'Invoke-vSphereApiClient',
    'Initialize-VcenterResourcePoolCreateSpec',
    'Initialize-VcenterResourcePoolResourceAllocationCreateSpec',
    'Initialize-VcenterResourcePoolSharesInfo'
)) {
    Assert-True "solution does not shadow genuine SDK command $sdkCommand" (
        $functionNames -notcontains $sdkCommand
    )
}

Import-Module $modulePath -Force
$module = Get-Module -Name 'VcfResourcePoolRollout'
$exports = @(
    $module.ExportedCommands.Keys | Sort-Object
)
Assert-Eq 'module exports exactly one command' 'Invoke-VcfResourcePoolRollout' ($exports -join ',')

# ---------------------------------------------------------------------------
# A loopback-only certificate for the fixture; the SDK always speaks HTTPS.
# ---------------------------------------------------------------------------

$scratch = Join-Path $PSScriptRoot '.verify-scratch'
if (Test-Path -LiteralPath $scratch) { Remove-Item -LiteralPath $scratch -Recurse -Force }
New-Item -ItemType Directory -Path $scratch | Out-Null

$certFile = Join-Path $scratch 'loopback-cert.pem'
$keyFile = Join-Path $scratch 'loopback-key.pem'
$mockPath = Join-Path $PSScriptRoot 'mock_vcenter.py'

$rsa = [System.Security.Cryptography.RSA]::Create(2048)
$certificateRequest = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new(
    'CN=127.0.0.1', $rsa,
    [System.Security.Cryptography.HashAlgorithmName]::SHA256,
    [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
$subjectAlternativeName =
    [System.Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
$subjectAlternativeName.AddIpAddress([System.Net.IPAddress]::Loopback)
$subjectAlternativeName.AddDnsName('localhost')
$certificateRequest.CertificateExtensions.Add($subjectAlternativeName.Build())
$notBefore = [System.DateTimeOffset]::new(2024, 1, 1, 0, 0, 0, [System.TimeSpan]::Zero)
$certificate = $certificateRequest.CreateSelfSigned($notBefore, $notBefore.AddYears(20))
Set-Content -LiteralPath $certFile -Value $certificate.ExportCertificatePem() -NoNewline
Set-Content -LiteralPath $keyFile -Value $rsa.ExportPkcs8PrivateKeyPem() -NoNewline

$credential = [pscredential]::new(
    'administrator@vsphere.local',
    (ConvertTo-SecureString 'dummy-vcenter-pass-90' -AsPlainText -Force))

$script:MockIndex = 0

function Start-MockVCenter {
    <#
        .SYNOPSIS
        Starts a fresh loopback vCenter so every scenario sees the same
        inventory and its own request log.
    #>
    param(
        [switch] $DuplicateParent,
        [switch] $FailSessionDelete
    )

    $script:MockIndex++
    $runDirectory = Join-Path $scratch "run-$script:MockIndex"
    New-Item -ItemType Directory -Path $runDirectory | Out-Null

    $portFile = Join-Path $runDirectory 'port.txt'
    $requestLog = Join-Path $runDirectory 'requests.jsonl'

    $arguments = @($mockPath, $certFile, $keyFile, $portFile, $requestLog)
    if ($DuplicateParent) { $arguments += '--duplicate-parent' }
    if ($FailSessionDelete) { $arguments += '--fail-session-delete' }

    $process = Start-Process -FilePath 'python3' `
        -ArgumentList $arguments `
        -PassThru -NoNewWindow `
        -RedirectStandardOutput (Join-Path $runDirectory 'server.out') `
        -RedirectStandardError (Join-Path $runDirectory 'server.err')

    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while (-not (Test-Path -LiteralPath $portFile -PathType Leaf)) {
        if ($process.HasExited -or [DateTime]::UtcNow -gt $deadline) {
            throw "the loopback vCenter did not start: $(Get-Content -LiteralPath (Join-Path $runDirectory 'server.err') -Raw -ErrorAction SilentlyContinue)"
        }
        Start-Sleep -Milliseconds 100
    }

    [pscustomobject] @{
        Process    = $process
        Port       = [int] (Get-Content -LiteralPath $portFile -Raw).Trim()
        RequestLog = $requestLog
    }
}

function Stop-MockVCenter {
    param([Parameter(Mandatory)] [object] $Mock)
    if (-not $Mock.Process.HasExited) {
        $Mock.Process.Kill()
        $Mock.Process.WaitForExit(5000) > $null
    }
}

# Runs one plan against its own loopback vCenter and hands back both the report
# the solution produced and everything it put on the wire.
function Invoke-Scenario {
    param(
        [Parameter(Mandatory)] [string] $PlanName,
        [pscredential] $ScenarioCredential = $credential,
        [bool] $UseSkipCertificateCheck = $true,
        [switch] $DuplicateParent,
        [switch] $FailSessionDelete
    )

    $mock = Start-MockVCenter -DuplicateParent:$DuplicateParent `
        -FailSessionDelete:$FailSessionDelete
    try {
        $authority = "127.0.0.1:$($mock.Port)"
        $planPath = Join-Path $PSScriptRoot (Join-Path 'plans' $PlanName)
        $report = $null
        $threw = $false
        $errorMessage = ''

        try {
            $parameters = @{
                Server     = $authority
                Credential = $ScenarioCredential
                PlanPath   = $planPath
            }
            if ($UseSkipCertificateCheck) { $parameters['SkipCertificateCheck'] = $true }
            $report = Invoke-VcfResourcePoolRollout @parameters
        } catch {
            $threw = $true
            $errorMessage = $_.Exception.Message
        }

        [pscustomobject] @{
            Authority    = $authority
            Report       = $report
            Threw        = $threw
            ErrorMessage = $errorMessage
            Log          = Get-RequestLog -Path $mock.RequestLog
        }
    } finally {
        Stop-MockVCenter -Mock $mock
    }
}

# Shared expectations: the session is opened once with basic auth, every later
# request presents the session token, the session is closed, and nothing off
# the contract is ever touched.
function Assert-SessionLifecycle {
    param(
        [Parameter(Mandatory)] [string] $Scenario,
        [Parameter(Mandatory)] [AllowNull()] [object] $Log
    )

    $records = @($Log)
    Assert-True "$Scenario opens at least a session and closes it" ($records.Count -ge 2)
    if ($records.Count -lt 2) { return }

    $first = $records[0]
    $last = $records[$records.Count - 1]

    Assert-Eq "$Scenario opens with Cis.Session_create" 'POST /api/session' "$($first.method) $($first.path)"
    Assert-Eq "$Scenario authenticates the session with basic auth" 'basic' $first.auth
    Assert-Eq "$Scenario session create succeeds" '201' "$($first.status)"

    Assert-Eq "$Scenario closes with Cis.Session_delete" 'DELETE /api/session' "$($last.method) $($last.path)"
    Assert-Eq "$Scenario session delete succeeds" '204' "$($last.status)"

    Assert-Eq "$Scenario opens exactly one session" '1' `
        "$(@($records | Where-Object { $_.method -eq 'POST' -and $_.path -eq '/api/session' }).Count)"
    Assert-Eq "$Scenario closes exactly one session" '1' `
        "$(@($records | Where-Object { $_.method -eq 'DELETE' -and $_.path -eq '/api/session' }).Count)"

    $unauthenticated = @($records | Select-Object -Skip 1 | Where-Object { -not $_.hasSessionHeader })
    Assert-Eq "$Scenario presents the session token on every later request" '' `
        (($unauthenticated | ForEach-Object { "$($_.method) $($_.path)" }) -join ',')

    $offContract = @($records | Where-Object { $_.offContract })
    Assert-Eq "$Scenario touches no operation outside the contract" '' `
        (($offContract | ForEach-Object { "$($_.method) $($_.path)" }) -join ',')

    $refused = @($records | Where-Object { $_.status -eq 400 -and -not $_.offContract })
    foreach ($record in $refused) {
        # A 400 here means the request contradicted the wire shape the
        # specification describes, not that the scenario expected a rejection.
        if ($record.method -eq 'POST' -and $record.path -eq '/api/vcenter/resource-pool') { continue }
        Assert-Eq "$Scenario sends no request the specification refuses" '' `
            "$($record.method) $($record.path)?$($record.rawQuery)"
    }
}

# The parent lookup is Vcenter.ResourcePool_list filtered by names alone: the
# other five filters are unset, so they may not appear as query keys at all.
function Assert-ParentLookup {
    param(
        [Parameter(Mandatory)] [string] $Scenario,
        [Parameter(Mandatory)] [object] $Record,
        [Parameter(Mandatory)] [string] $ParentName
    )

    Assert-Eq "$Scenario resolves the parent with Vcenter.ResourcePool_list" `
        'GET /api/vcenter/resource-pool' "$($Record.method) $($Record.path)"
    Assert-Eq "$Scenario sends names as the only query key" 'names' (Get-QueryKeys -Record $Record)
    Assert-Eq "$Scenario filters the lookup by the parent pool name" $ParentName `
        ((@(Get-MemberOrNull -InputObject $Record.query -Name 'names')) -join ',')
    Assert-Eq "$Scenario parent lookup succeeds" '200' "$($Record.status)"
}

function Assert-CreateBody {
    param(
        [Parameter(Mandatory)] [string] $Scenario,
        [Parameter(Mandatory)] [object] $Record,
        [Parameter(Mandatory)] [string] $ExpectedJson
    )

    Assert-Eq "$Scenario issues Vcenter.ResourcePool_create" `
        'POST /api/vcenter/resource-pool' "$($Record.method) $($Record.path)"
    Assert-Eq "$Scenario sends no query parameters on the create" '' (Get-QueryKeys -Record $Record)
    Assert-Eq "$Scenario sends the exact create body" `
        (ConvertTo-CanonicalJson -Json $ExpectedJson) (ConvertTo-Canonical -Node $Record.body)
}

$parentId = 'resgroup-10'
$stagingId = 'resgroup-20'

try {

# ---------------------------------------------------------------------------
# Scenario 1: the whole plan applies.
# ---------------------------------------------------------------------------

$complete = Invoke-Scenario -PlanName 'rollout-complete.json'
$log = @($complete.Log)

Assert-True 'complete rollout returns a report' (-not $complete.Threw)
Assert-Eq 'complete rollout makes exactly seven requests' '7' "$($log.Count)"
Assert-Eq 'complete rollout wire trace' `
    'POST /api/session 201 | GET /api/vcenter/resource-pool 200 | POST /api/vcenter/resource-pool 201 | POST /api/vcenter/resource-pool 201 | POST /api/vcenter/resource-pool 201 | POST /api/vcenter/resource-pool 201 | DELETE /api/session 204' `
    (Format-Trace -Log $log)
Assert-SessionLifecycle -Scenario 'complete rollout' -Log $log

if ($log.Count -eq 7) {
    Assert-ParentLookup -Scenario 'complete rollout' -Record $log[1] -ParentName 'Production'

    # No CPU or memory tuning at all, so both allocations are absent entirely.
    Assert-CreateBody -Scenario 'complete rollout pool 1' -Record $log[2] `
        -ExpectedJson "{`"name`":`"edge-web`",`"parent`":`"$parentId`"}"

    # Only a CPU limit, so cpu_allocation carries that one member and the other
    # three stay off the wire, and memory_allocation is absent.
    Assert-CreateBody -Scenario 'complete rollout pool 2' -Record $log[3] `
        -ExpectedJson "{`"name`":`"edge-api`",`"parent`":`"$parentId`",`"cpu_allocation`":{`"limit`":8000}}"

    Assert-CreateBody -Scenario 'complete rollout pool 3' -Record $log[4] `
        -ExpectedJson "{`"name`":`"edge-cache`",`"parent`":`"$parentId`",`"cpu_allocation`":{`"shares`":{`"level`":`"CUSTOM`",`"shares`":1500}},`"memory_allocation`":{`"reservation`":8192,`"expandable_reservation`":false}}"

    # The plan carries a cpuAllocation block that tunes nothing.  An allocation
    # with no members is not the same as a default allocation, so it has to be
    # left off the wire entirely rather than sent as an empty object.
    Assert-CreateBody -Scenario 'complete rollout pool 4' -Record $log[5] `
        -ExpectedJson "{`"name`":`"edge-mq`",`"parent`":`"$parentId`",`"memory_allocation`":{`"limit`":16384}}"
}

$report = $complete.Report
Assert-Eq 'complete rollout report property order' `
    'Server,ParentPoolName,ParentPoolId,PlannedCount,CreatedCount,Created,Failed,NotAttempted,Succeeded' `
    ((@($report.PSObject.Properties.Name)) -join ',')
Assert-Eq 'complete rollout reports the server it changed' $complete.Authority $report.Server
Assert-Eq 'complete rollout reports the parent pool name' 'Production' $report.ParentPoolName
Assert-Eq 'complete rollout reports the parent pool identifier' $parentId $report.ParentPoolId
Assert-Eq 'complete rollout reports the planned count' '4' "$($report.PlannedCount)"
Assert-Eq 'complete rollout reports the created count' '4' "$($report.CreatedCount)"
Assert-Eq 'complete rollout reports the created pools in plan order' 'edge-web,edge-api,edge-cache,edge-mq' `
    ((@($report.Created) | ForEach-Object { $_.Name }) -join ',')
Assert-Eq 'complete rollout reports the identifiers vCenter returned' 'resgroup-100,resgroup-101,resgroup-102,resgroup-103' `
    ((@($report.Created) | ForEach-Object { $_.ResourcePoolId }) -join ',')
Assert-Eq 'complete rollout created row property order' 'Name,ResourcePoolId' `
    ((@($report.Created)[0].PSObject.Properties.Name) -join ',')
Assert-True 'complete rollout reports no failure' ($null -eq $report.Failed)
Assert-Eq 'complete rollout leaves nothing unattempted' '' ((@($report.NotAttempted)) -join ',')
Assert-Eq 'complete rollout reports success' 'True' "$($report.Succeeded)"

# ---------------------------------------------------------------------------
# Scenario 2: the third pool collides with one that is already there.
# ---------------------------------------------------------------------------

$collision = Invoke-Scenario -PlanName 'rollout-name-collision.json'
$log = @($collision.Log)

Assert-True 'collision rollout returns a report rather than throwing' (-not $collision.Threw)
Assert-Eq 'collision rollout stops on the failed step' `
    'POST /api/session 201 | GET /api/vcenter/resource-pool 200 | POST /api/vcenter/resource-pool 201 | POST /api/vcenter/resource-pool 201 | POST /api/vcenter/resource-pool 400 | DELETE /api/session 204' `
    (Format-Trace -Log $log)
Assert-SessionLifecycle -Scenario 'collision rollout' -Log $log

$createNames = @(
    $log |
        Where-Object { $_.method -eq 'POST' -and $_.path -eq '/api/vcenter/resource-pool' } |
        ForEach-Object { (Get-MemberOrNull -InputObject $_.body -Name 'name') }
)
Assert-Eq 'collision rollout attempts only the pools up to the failure' `
    'batch-alpha,batch-beta,platform-shared' ($createNames -join ',')

if ($log.Count -eq 6) {
    Assert-ParentLookup -Scenario 'collision rollout' -Record $log[1] -ParentName 'Production'
    Assert-CreateBody -Scenario 'collision rollout pool 1' -Record $log[2] `
        -ExpectedJson "{`"name`":`"batch-alpha`",`"parent`":`"$parentId`"}"
    Assert-CreateBody -Scenario 'collision rollout pool 2' -Record $log[3] `
        -ExpectedJson "{`"name`":`"batch-beta`",`"parent`":`"$parentId`",`"cpu_allocation`":{`"reservation`":4000,`"expandable_reservation`":true}}"
    # A shares block that only sets level: the optional shares count stays off.
    Assert-CreateBody -Scenario 'collision rollout pool 3' -Record $log[4] `
        -ExpectedJson "{`"name`":`"platform-shared`",`"parent`":`"$parentId`",`"memory_allocation`":{`"shares`":{`"level`":`"HIGH`"}}}"
}

$report = $collision.Report
Assert-Eq 'collision rollout reports the planned count' '5' "$($report.PlannedCount)"
Assert-Eq 'collision rollout reports the created count' '2' "$($report.CreatedCount)"
Assert-Eq 'collision rollout reports the pools that were created' 'batch-alpha,batch-beta' `
    ((@($report.Created) | ForEach-Object { $_.Name }) -join ',')
Assert-Eq 'collision rollout reports the identifiers of the pools that were created' `
    'resgroup-100,resgroup-101' ((@($report.Created) | ForEach-Object { $_.ResourcePoolId }) -join ',')
Assert-True 'collision rollout reports a failure' ($null -ne $report.Failed)
Assert-Eq 'collision rollout failure property order' 'Name,Status,ErrorType,Message' `
    ((@($report.Failed.PSObject.Properties.Name)) -join ',')
Assert-Eq 'collision rollout names the pool that failed' 'platform-shared' $report.Failed.Name
Assert-Eq 'collision rollout reports the status vCenter returned' '400' "$($report.Failed.Status)"
Assert-Eq 'collision rollout reports the vAPI error type' 'INVALID_ARGUMENT' $report.Failed.ErrorType
Assert-Eq 'collision rollout reports the message vCenter returned' `
    $log[4].responseBody.messages[0].default_message $report.Failed.Message
Assert-Eq 'collision rollout reports the pools it never attempted' 'batch-delta,batch-epsilon' `
    ((@($report.NotAttempted)) -join ',')
Assert-Eq 'collision rollout does not report success' 'False' "$($report.Succeeded)"

# ---------------------------------------------------------------------------
# Scenario 3: a different failure, at a different position, with a different
# status and error type, so the report cannot be a fixed answer.
# ---------------------------------------------------------------------------

$overCommit = Invoke-Scenario -PlanName 'rollout-over-commit.json'
$log = @($overCommit.Log)

Assert-True 'over-commit rollout returns a report rather than throwing' (-not $overCommit.Threw)
Assert-Eq 'over-commit rollout stops on the failed step' `
    'POST /api/session 201 | GET /api/vcenter/resource-pool 200 | POST /api/vcenter/resource-pool 201 | POST /api/vcenter/resource-pool 500 | DELETE /api/session 204' `
    (Format-Trace -Log $log)
Assert-SessionLifecycle -Scenario 'over-commit rollout' -Log $log

if ($log.Count -eq 5) {
    Assert-ParentLookup -Scenario 'over-commit rollout' -Record $log[1] -ParentName 'Staging'
    Assert-CreateBody -Scenario 'over-commit rollout pool 1' -Record $log[2] `
        -ExpectedJson "{`"name`":`"stage-one`",`"parent`":`"$stagingId`",`"cpu_allocation`":{`"reservation`":2000}}"
    Assert-CreateBody -Scenario 'over-commit rollout pool 2' -Record $log[3] `
        -ExpectedJson "{`"name`":`"stage-two`",`"parent`":`"$stagingId`",`"cpu_allocation`":{`"reservation`":60000}}"
}

$report = $overCommit.Report
Assert-Eq 'over-commit rollout resolves the other parent pool' $stagingId $report.ParentPoolId
Assert-Eq 'over-commit rollout reports the planned count' '3' "$($report.PlannedCount)"
Assert-Eq 'over-commit rollout reports the created count' '1' "$($report.CreatedCount)"
Assert-Eq 'over-commit rollout reports the pool that was created' 'stage-one' `
    ((@($report.Created) | ForEach-Object { $_.Name }) -join ',')
Assert-Eq 'over-commit rollout reports the identifier of the pool that was created' 'resgroup-100' `
    ((@($report.Created) | ForEach-Object { $_.ResourcePoolId }) -join ',')
Assert-Eq 'over-commit rollout names the pool that failed' 'stage-two' $report.Failed.Name
Assert-Eq 'over-commit rollout reports the status vCenter returned' '500' "$($report.Failed.Status)"
Assert-Eq 'over-commit rollout reports the vAPI error type' 'UNABLE_TO_ALLOCATE_RESOURCE' `
    $report.Failed.ErrorType
Assert-Eq 'over-commit rollout reports the message vCenter returned' `
    $log[3].responseBody.messages[0].default_message $report.Failed.Message
Assert-Eq 'over-commit rollout reports the pool it never attempted' 'stage-three' `
    ((@($report.NotAttempted)) -join ',')
Assert-Eq 'over-commit rollout does not report success' 'False' "$($report.Succeeded)"

# ---------------------------------------------------------------------------
# Scenario 4: the parent cannot be resolved, so nothing may be changed at all.
# ---------------------------------------------------------------------------

$unknownParent = Invoke-Scenario -PlanName 'rollout-unknown-parent.json'
$log = @($unknownParent.Log)

Assert-True 'unknown parent fails the run' $unknownParent.Threw
Assert-True 'unknown parent names the pool it could not resolve' (
    $unknownParent.ErrorMessage -match 'Archive'
)
Assert-Eq 'unknown parent changes nothing' `
    'POST /api/session 201 | GET /api/vcenter/resource-pool 200 | DELETE /api/session 204' `
    (Format-Trace -Log $log)
Assert-SessionLifecycle -Scenario 'unknown parent' -Log $log
Assert-Eq 'unknown parent issues no Vcenter.ResourcePool_create' '0' `
    "$(@($log | Where-Object { $_.method -eq 'POST' -and $_.path -eq '/api/vcenter/resource-pool' }).Count)"

if ($log.Count -ge 2) {
    Assert-ParentLookup -Scenario 'unknown parent' -Record $log[1] -ParentName 'Archive'
}

# ---------------------------------------------------------------------------
# Scenario 5: the same plan against a fresh vCenter has to behave identically.
# ---------------------------------------------------------------------------

$repeat = Invoke-Scenario -PlanName 'rollout-name-collision.json'
Assert-Eq 'repeated rollout puts the same requests on the wire' `
    (Format-Trace -Log $collision.Log) (Format-Trace -Log $repeat.Log)
Assert-Eq 'repeated rollout reports the same created pools' `
    ((@($collision.Report.Created) | ForEach-Object { "$($_.Name)=$($_.ResourcePoolId)" }) -join ',') `
    ((@($repeat.Report.Created) | ForEach-Object { "$($_.Name)=$($_.ResourcePoolId)" }) -join ',')
Assert-Eq 'repeated rollout reports the same failure' `
    "$($collision.Report.Failed.Name)/$($collision.Report.Failed.Status)/$($collision.Report.Failed.ErrorType)" `
    "$($repeat.Report.Failed.Name)/$($repeat.Report.Failed.Status)/$($repeat.Report.Failed.ErrorType)"

# ---------------------------------------------------------------------------
# Scenario 6: a parent name that resolves to more than one identifier is
# ambiguous, so the run has to stop before creating anything.
# ---------------------------------------------------------------------------

$duplicateParent = Invoke-Scenario -PlanName 'rollout-complete.json' -DuplicateParent
$log = @($duplicateParent.Log)
Assert-True 'duplicate parent fails the run' $duplicateParent.Threw
Assert-True 'duplicate parent error names the ambiguous pool' (
    $duplicateParent.ErrorMessage -match 'Production'
)
Assert-Eq 'duplicate parent changes nothing' `
    'POST /api/session 201 | GET /api/vcenter/resource-pool 200 | DELETE /api/session 204' `
    (Format-Trace -Log $log)
Assert-SessionLifecycle -Scenario 'duplicate parent' -Log $log
Assert-Eq 'duplicate parent issues no Vcenter.ResourcePool_create' '0' `
    "$(@($log | Where-Object { $_.method -eq 'POST' -and $_.path -eq '/api/vcenter/resource-pool' }).Count)"

# ---------------------------------------------------------------------------
# Scenario 7: session creation must actually use the supplied credential.
# ---------------------------------------------------------------------------

$badCredential = [pscredential]::new(
    'administrator@vsphere.local',
    (ConvertTo-SecureString 'not-the-vcenter-password' -AsPlainText -Force))
$refusedSession = Invoke-Scenario -PlanName 'rollout-complete.json' `
    -ScenarioCredential $badCredential
$log = @($refusedSession.Log)
Assert-True 'refused session fails the run' $refusedSession.Threw
Assert-Eq 'refused session sends only the authentication request' `
    'POST /api/session 401' (Format-Trace -Log $log)
if ($log.Count -eq 1) {
    Assert-Eq 'refused session uses basic auth' 'basic' $log[0].auth
    Assert-Eq 'refused session uses Cis.Session_create' 'Cis.Session_create' $log[0].operationId
}

$badUsername = [pscredential]::new(
    'not-the-vcenter-user',
    (ConvertTo-SecureString 'dummy-vcenter-pass-90' -AsPlainText -Force))
$refusedUsername = Invoke-Scenario -PlanName 'rollout-complete.json' `
    -ScenarioCredential $badUsername
Assert-True 'session creation uses the supplied credential username' $refusedUsername.Threw
Assert-Eq 'wrong username sends only the refused authentication request' `
    'POST /api/session 401' (Format-Trace -Log $refusedUsername.Log)

# ---------------------------------------------------------------------------
# Scenario 8: SkipCertificateCheck is a real control, not an unconditional
# relaxation hidden in the module.  Without it, the self-signed fixture is
# rejected before any HTTP request reaches vCenter.
# ---------------------------------------------------------------------------

$certificateRefused = Invoke-Scenario -PlanName 'rollout-complete.json' `
    -UseSkipCertificateCheck:$false
Assert-True 'certificate validation is enforced when SkipCertificateCheck is absent' `
    $certificateRefused.Threw
Assert-Eq 'certificate refusal reaches no vCenter operation' '' `
    (Format-Trace -Log $certificateRefused.Log)

# ---------------------------------------------------------------------------
# Scenario 9: teardown is attempted after a partial failure, but a teardown
# failure must not replace the rollout report.
# ---------------------------------------------------------------------------

$teardownFailure = Invoke-Scenario -PlanName 'rollout-name-collision.json' `
    -FailSessionDelete
$log = @($teardownFailure.Log)
Assert-True 'failed teardown does not make the partial rollout throw' (-not $teardownFailure.Threw)
Assert-Eq 'failed teardown is still attempted after the failed pool' `
    'POST /api/session 201 | GET /api/vcenter/resource-pool 200 | POST /api/vcenter/resource-pool 201 | POST /api/vcenter/resource-pool 201 | POST /api/vcenter/resource-pool 400 | DELETE /api/session 503' `
    (Format-Trace -Log $log)
Assert-Eq 'failed teardown preserves the created-pool report' 'batch-alpha,batch-beta' `
    ((@($teardownFailure.Report.Created) | ForEach-Object Name) -join ',')
Assert-Eq 'failed teardown preserves the failed-pool report' 'platform-shared/400/INVALID_ARGUMENT' `
    "$($teardownFailure.Report.Failed.Name)/$($teardownFailure.Report.Failed.Status)/$($teardownFailure.Report.Failed.ErrorType)"
Assert-Eq 'failed teardown preserves the not-attempted report' 'batch-delta,batch-epsilon' `
    ((@($teardownFailure.Report.NotAttempted)) -join ',')

} finally {
    if (Test-Path -LiteralPath $scratch) {
        Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------------------

if ($script:Failures -gt 0) {
    Write-Output "FAILED $script:Failures of $script:Checks checks"
    exit 1
}

Write-Output "PASS $script:Checks checks"
exit 0
