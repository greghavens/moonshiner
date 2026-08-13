<#
    Protected verifier.

    Starts the contract-pinned loopback mock on an ephemeral 127.0.0.1 port, runs
    each scenario in its own child process, then asserts the exact request wire
    shape from the mock's request log.

    No live VMware endpoint is contacted.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$RepoRoot     = Split-Path -Parent $PSScriptRoot
$ContractPath = Join-Path $RepoRoot 'docs/contract.json'
$MockScript   = Join-Path $RepoRoot 'tools/contractmock/Start-VcfOpsContractMock.ps1'
$ScenarioRun  = Join-Path $PSScriptRoot 'Invoke-Scenario.ps1'
$ManifestPath = Join-Path $RepoRoot 'src/VcfOps.AdapterOnboarding/VcfOps.AdapterOnboarding.psd1'
$ModulePath   = Join-Path $RepoRoot 'src/VcfOps.AdapterOnboarding/VcfOps.AdapterOnboarding.psm1'

$Contract   = Get-Content -LiteralPath $ContractPath -Raw | ConvertFrom-Json
$BasePath   = $Contract.basePath
$AuthHeader = 'OpsToken a4d63c0e-2f18-4d0a-9b56-70c1c1e4a2f7'
$CreatedId  = '725cbdae-812e-4e98-9972-53c58f51661b'

# ---------------------------------------------------------------------------
# Minimal assertion harness
# ---------------------------------------------------------------------------
$script:Failures = [System.Collections.Generic.List[string]]::new()
$script:Passed = 0
$script:Scenario = '(setup)'

function Fail {
    param([string] $Message)
    $script:Failures.Add(('[{0}] {1}' -f $script:Scenario, $Message))
    Write-Host ('  FAIL  {0}' -f $Message)
}

function Pass {
    param([string] $Message)
    $script:Passed++
    Write-Host ('  ok    {0}' -f $Message)
}

function Assert-True {
    param([bool] $Condition, [string] $Message)
    if ($Condition) { Pass $Message } else { Fail $Message }
}

function Assert-Equal {
    param($Expected, $Actual, [string] $Message)

    # Type-aware: a JSON string "5" must not satisfy an expected number 5, but a
    # JSON number that lands as Int64 must satisfy an expected Int32.
    $ok = $false
    if ($null -eq $Expected) {
        $ok = ($null -eq $Actual)
    } elseif ($Expected -is [string]) {
        $ok = ($Actual -is [string]) -and [string]::Equals($Expected, $Actual, [System.StringComparison]::Ordinal)
    } elseif ($Expected -is [bool]) {
        $ok = ($Actual -is [bool]) -and ($Expected -eq $Actual)
    } elseif ($Expected -is [ValueType]) {
        $ok = ($Actual -is [ValueType]) -and ($Actual -isnot [bool]) -and
              ([decimal]$Expected -eq [decimal]$Actual)
    } else {
        $ok = $Expected.Equals($Actual)
    }

    if ($ok) {
        Pass $Message
    } else {
        Fail ('{0} (expected <{1}>, actual <{2}>)' -f $Message, $Expected, $Actual)
    }
}

function Assert-Sequence {
    param([string[]] $Expected, [string[]] $Actual, [string] $Message)
    $e = ($Expected -join ' -> ')
    $a = ($Actual -join ' -> ')
    Assert-Equal -Expected $e -Actual $a -Message $Message
}

function Assert-ResultShape {
    param($Outcome)

    $actual = @($Outcome.resultProperties | Sort-Object)
    foreach ($name in @('AdapterInstanceId', 'Message', 'PrecheckPassed', 'Status')) {
        Assert-True -Condition ($actual -contains $name) `
            -Message ('the result carries the {0} property' -f $name)
    }
}

# The implementation constraint is part of the deliverable, not merely a style
# preference: the workflow must stay on the supported SDK and preserve the
# supplied public command contract. Parse the submitted artifact so a hand-built
# HTTP client or a changed signature cannot pass solely by imitating the wire log.
function Assert-ImplementationContract {
    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        $ModulePath, [ref]$tokens, [ref]$parseErrors)

    Assert-Equal -Expected 0 -Actual @($parseErrors).Count `
        -Message 'the implementation module parses without syntax errors'
    if (@($parseErrors).Count -ne 0) { return }

    $functions = @($ast.FindAll({
                param($node)
                $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -eq 'Register-VcfOpsAdapterInstance'
            }, $true))
    Assert-Equal -Expected 1 -Actual $functions.Count `
        -Message 'the module defines Register-VcfOpsAdapterInstance exactly once'
    if ($functions.Count -ne 1) { return }

    $function = $functions[0]
    $expectedParameters = @'
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string] $Server,
    [Parameter(Mandatory)]
    [int] $Port,
    [Parameter(Mandatory)]
    [pscredential] $Credential,
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string] $Name,
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string] $AdapterKindKey,
    [ValidateSet('http', 'https')]
    [string] $Protocol = 'https',
    [ValidateNotNullOrEmpty()]
    [string] $AuthSource = 'local',
    [System.Collections.Specialized.OrderedDictionary] $ResourceIdentifier,
    [string] $Description,
    [string] $CollectorId,
    [Nullable[int]] $MonitoringInterval,
    [switch] $SkipCertificateCheck
)
'@
    $expectedSignature = [regex]::Replace($expectedParameters, '\s+', '')
    $actualSignature = [regex]::Replace($function.Body.ParamBlock.Extent.Text, '\s+', '')
    Assert-Equal -Expected $expectedSignature -Actual $actualSignature `
        -Message 'the supplied parameter block is preserved exactly'

    $commandAsts = @($function.Body.FindAll({
                param($node)
                $node -is [System.Management.Automation.Language.CommandAst]
            }, $true))
    $commandNames = @($commandAsts | ForEach-Object { $_.GetCommandName() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    foreach ($required in @(
            'Connect-VcfOpsServer',
            'Invoke-VcfOpsTestConnection',
            'Invoke-VcfOpsCreateAdapterInstance',
            'Disconnect-VcfOpsServer')) {
        Assert-True -Condition ($commandNames -contains $required) `
            -Message ('the implementation uses the SDK command {0}' -f $required)
    }

    $forbiddenCommands = @(
        'Invoke-RestMethod', 'irm',
        'Invoke-WebRequest', 'iwr',
        'curl', 'curl.exe', 'wget', 'wget.exe',
        'Start-BitsTransfer', 'Add-Type'
    )
    $rawHttpCommands = @($commandNames | Where-Object { $forbiddenCommands -contains $_ })
    Assert-Equal -Expected 0 -Actual $rawHttpCommands.Count `
        -Message 'the implementation does not hand-roll requests with an HTTP command'

    $networkTypeAsts = @($function.Body.FindAll({
                param($node)
                ($node -is [System.Management.Automation.Language.TypeExpressionAst] -or
                 $node -is [System.Management.Automation.Language.TypeConstraintAst]) -and
                $node.TypeName.FullName -like 'System.Net.*'
            }, $true))
    $newNetworkObjects = @($commandAsts | Where-Object {
            $_.GetCommandName() -eq 'New-Object' -and $_.Extent.Text -match '(?i)System\.Net\.'
        })
    Assert-Equal -Expected 0 -Actual ($networkTypeAsts.Count + $newNetworkObjects.Count) `
        -Message 'the implementation does not construct a raw System.Net client'

    $shadowedSdkCommands = @($ast.FindAll({
                param($node)
                $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                @(
                    'Connect-VcfOpsServer',
                    'Invoke-VcfOpsTestConnection',
                    'Invoke-VcfOpsCreateAdapterInstance',
                    'Disconnect-VcfOpsServer'
                ) -contains $node.Name
            }, $true))
    Assert-Equal -Expected 0 -Actual $shadowedSdkCommands.Count `
        -Message 'the implementation does not replace the required SDK commands'

    $allCommandAsts = @($ast.FindAll({
                param($node)
                $node -is [System.Management.Automation.Language.CommandAst]
            }, $true))
    $otherImports = @($allCommandAsts | Where-Object {
            $_.GetCommandName() -eq 'Import-Module' -and
            $_.Extent.Text -notmatch "(?i)VMware\.Sdk\.Vcf\.Ops"
        })
    $otherRequiredModules = @()
    $requiredAssemblies = @()
    if ($null -ne $ast.ScriptRequirements) {
        $otherRequiredModules = @($ast.ScriptRequirements.RequiredModules | Where-Object {
                $_.Name -ne 'VMware.Sdk.Vcf.Ops'
            })
        $requiredAssemblies = @($ast.ScriptRequirements.RequiredAssemblies)
    }
    $otherUsingModules = @($ast.FindAll({
                param($node)
                $node -is [System.Management.Automation.Language.UsingStatementAst] -and
                $node.UsingStatementKind -eq [System.Management.Automation.Language.UsingStatementKind]::Module -and
                [string]$node.Name.Value -ne 'VMware.Sdk.Vcf.Ops'
            }, $true))
    $additionalDependencies = $otherImports.Count + $otherRequiredModules.Count +
        $otherUsingModules.Count + $requiredAssemblies.Count
    Assert-Equal -Expected 0 -Actual $additionalDependencies `
        -Message 'the implementation does not import another module dependency'

    $bodyText = $function.Body.Extent.Text
    Assert-True -Condition (
        $bodyText -match '(?i)\$SkipCertificateCheck' -and
        $bodyText -match '(?i)IgnoreInvalidCertificate') `
        -Message 'SkipCertificateCheck is mapped to the SDK certificate-bypass option'
}

# ---------------------------------------------------------------------------
# Mock lifecycle
# ---------------------------------------------------------------------------
function Get-FreeLoopbackPort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { return $listener.LocalEndpoint.Port } finally { $listener.Stop() }
}

function Start-ContractMock {
    param([string] $MockScenario, [string] $WorkDir)

    for ($attempt = 1; $attempt -le 8; $attempt++) {
        $port = Get-FreeLoopbackPort
        $logPath = Join-Path $WorkDir ('requests-{0}.jsonl' -f $attempt)
        $readyPath = Join-Path $WorkDir ('ready-{0}.txt' -f $attempt)
        Remove-Item -LiteralPath $readyPath -ErrorAction SilentlyContinue

        $process = Start-Process -FilePath (Get-Process -Id $PID).Path -PassThru -NoNewWindow `
            -ArgumentList @(
            '-NoProfile', '-NonInteractive', '-File', $MockScript,
            '-ContractPath', $ContractPath,
            '-Port', $port,
            '-LogPath', $logPath,
            '-ReadyPath', $readyPath,
            '-Scenario', $MockScenario
        )

        $deadline = [datetime]::UtcNow.AddSeconds(20)
        while ([datetime]::UtcNow -lt $deadline) {
            if (Test-Path -LiteralPath $readyPath) {
                return [pscustomobject]@{ Process = $process; Port = $port; LogPath = $logPath }
            }
            if ($process.HasExited) { break }
            Start-Sleep -Milliseconds 100
        }

        if (-not $process.HasExited) { $process.Kill() }
    }
    throw 'Unable to start the contract mock on a loopback port.'
}

function Stop-ContractMock {
    param($Mock)
    if ($Mock -and -not $Mock.Process.HasExited) {
        $Mock.Process.Kill()
        $Mock.Process.WaitForExit(10000) | Out-Null
    }
}

function Read-RequestLog {
    param([string] $LogPath)
    if (-not (Test-Path -LiteralPath $LogPath)) { return @() }
    $records = [System.Collections.Generic.List[object]]::new()
    foreach ($line in (Get-Content -LiteralPath $LogPath)) {
        if (-not [string]::IsNullOrWhiteSpace($line)) { $records.Add(($line | ConvertFrom-Json)) }
    }
    return @($records.ToArray())
}

# ---------------------------------------------------------------------------
# Wire-shape helpers
# ---------------------------------------------------------------------------
function Get-BodyKeys {
    param($Record)
    if ([string]::IsNullOrWhiteSpace($Record.body)) { return @() }
    try {
        return @(($Record.body | ConvertFrom-Json).PSObject.Properties.Name)
    } catch {
        Fail ('request #{0} body is not valid JSON: {1}' -f $Record.sequence, $Record.body)
        return @()
    }
}

function Get-BodyObject {
    param($Record)
    if ([string]::IsNullOrWhiteSpace($Record.body)) { return $null }
    try { return ($Record.body | ConvertFrom-Json) } catch { return $null }
}

function Get-HeaderValues {
    param($Record, [string] $Name)
    $property = $Record.headers.PSObject.Properties[$Name.ToLowerInvariant()]
    if ($null -eq $property) { return @() }
    return @($property.Value)
}

# Recursively reports every JSON path whose value is null, an empty string or an
# empty array -- i.e. an optional field that was sent empty instead of omitted.
function Find-EmptyMembers {
    param($Node, [string] $Path = '$')

    $found = [System.Collections.Generic.List[string]]::new()
    if ($null -eq $Node) {
        $found.Add($Path)
        return $found
    }
    if ($Node -is [string]) {
        if ($Node.Length -eq 0) { $found.Add($Path) }
        return $found
    }
    if ($Node -is [System.Collections.IEnumerable]) {
        $items = @($Node)
        if ($items.Count -eq 0) { $found.Add($Path) }
        for ($i = 0; $i -lt $items.Count; $i++) {
            foreach ($hit in (Find-EmptyMembers -Node $items[$i] -Path ('{0}[{1}]' -f $Path, $i))) {
                $found.Add($hit)
            }
        }
        return $found
    }
    if ($Node -is [psobject] -and $Node.PSObject.Properties.Name.Count -gt 0 -and
        $Node.GetType().Name -eq 'PSCustomObject') {
        foreach ($property in $Node.PSObject.Properties) {
            foreach ($hit in (Find-EmptyMembers -Node $property.Value -Path ('{0}.{1}' -f $Path, $property.Name))) {
                $found.Add($hit)
            }
        }
    }
    return $found
}

function Assert-OnContract {
    param($Records)

    $offContract = @($Records | Where-Object { $null -ne $_ -and -not $_.operationId })
    if ($offContract.Count -eq 0) {
        Pass 'every request targeted an operation named in the contract'
    } else {
        $targets = ($offContract | ForEach-Object { '{0} {1}' -f $_.method, $_.rawTarget }) -join ', '
        Fail ('off-contract request(s) issued: {0}' -f $targets)
    }
}

function Assert-AcquireToken {
    param($Record)

    Assert-Equal -Expected 'POST' -Actual $Record.method -Message 'acquireToken uses POST'
    Assert-Equal -Expected ('{0}/api/auth/token/acquire' -f $BasePath) -Actual $Record.normalizedTarget `
        -Message 'acquireToken target carries no query string'
    Assert-Equal -Expected 0 -Actual @(Get-HeaderValues -Record $Record -Name 'Authorization').Count `
        -Message 'acquireToken is unauthenticated'

    $keys = @(Get-BodyKeys -Record $Record | Sort-Object)
    Assert-Sequence -Expected @('authSource', 'password', 'username') -Actual $keys `
        -Message 'acquireToken body carries exactly username, password and authSource'

    $body = Get-BodyObject -Record $Record
    if ($body) {
        Assert-Equal -Expected 'svc-vcfops' -Actual $body.username -Message 'acquireToken sends the supplied user name'
        Assert-Equal -Expected 'Precheck!23' -Actual $body.password -Message 'acquireToken sends the supplied password'
        Assert-Equal -Expected 'local' -Actual $body.authSource -Message 'acquireToken sends the supplied auth source'
    }
}

function Assert-AuthenticatedRequest {
    param($Record, [string] $Label)

    $values = @(Get-HeaderValues -Record $Record -Name 'Authorization')
    Assert-Equal -Expected 1 -Actual $values.Count -Message ('{0} carries exactly one Authorization header' -f $Label)
    if ($values.Count -eq 1) {
        Assert-Equal -Expected $AuthHeader -Actual $values[0] `
            -Message ('{0} presents the token issued by acquireToken with the OpsToken prefix' -f $Label)
    }
}

function Assert-JsonRequest {
    param($Record, [string] $Label)

    $values = @(Get-HeaderValues -Record $Record -Name 'Content-Type')
    Assert-Equal -Expected 1 -Actual $values.Count -Message ('{0} carries exactly one Content-Type header' -f $Label)
    if ($values.Count -eq 1) {
        Assert-True -Condition ($values[0] -like 'application/json*') `
            -Message ('{0} sends a JSON media type' -f $Label)
    }
    Assert-True -Condition ($Record.bodyByteCount -gt 0) -Message ('{0} sends a request body' -f $Label)
}

function Assert-NoEmptyMembers {
    param($Record, [string] $Label)

    $body = Get-BodyObject -Record $Record
    if ($null -eq $body) {
        Fail ('{0} body could not be parsed as JSON' -f $Label)
        return
    }
    $empty = @(Find-EmptyMembers -Node $body)
    if ($empty.Count -eq 0) {
        Pass ('{0} omits every unset optional field instead of sending it empty' -f $Label)
    } else {
        Fail ('{0} sent empty values at: {1}' -f $Label, ($empty -join ', '))
    }
}

# ---------------------------------------------------------------------------
# Scenario driver
# ---------------------------------------------------------------------------
function Invoke-Scenario {
    param([string] $ScenarioName, [string] $MockScenario)

    $workDir = Join-Path ([System.IO.Path]::GetTempPath()) ('vcfops-verify-{0}' -f [guid]::NewGuid())
    New-Item -ItemType Directory -Path $workDir -Force | Out-Null
    $mock = $null
    try {
        $mock = Start-ContractMock -MockScenario $MockScenario -WorkDir $workDir
        $resultPath = Join-Path $workDir 'result.json'

        $process = Start-Process -FilePath (Get-Process -Id $PID).Path -PassThru -NoNewWindow -Wait `
            -ArgumentList @(
            '-NoProfile', '-NonInteractive', '-File', $ScenarioRun,
            '-ManifestPath', $ManifestPath,
            '-Port', $mock.Port,
            '-Scenario', $ScenarioName,
            '-ResultPath', $resultPath
        )

        $outcome = $null
        if (Test-Path -LiteralPath $resultPath) {
            $outcome = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
        }

        # Give the mock a moment to finish flushing the final log line.
        Start-Sleep -Milliseconds 200
        $records = Read-RequestLog -LogPath $mock.LogPath

        return [pscustomobject]@{
            Outcome  = $outcome
            Records  = @($records)
            ExitCode = $process.ExitCode
        }
    } finally {
        Stop-ContractMock -Mock $mock
        Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Get-Record {
    param($Records, [string] $OperationId)
    return @($Records | Where-Object { $null -ne $_ -and $_.operationId -eq $OperationId })
}

function Assert-SessionRequests {
    param($Records)

    $version = Get-Record -Records $Records -OperationId 'getCurrentVersionOfServer'
    $release = Get-Record -Records $Records -OperationId 'releaseToken'
    Assert-Equal -Expected 1 -Actual $version.Count `
        -Message 'getCurrentVersionOfServer is called exactly once'
    Assert-Equal -Expected 1 -Actual $release.Count `
        -Message 'releaseToken is called exactly once'

    if ($version.Count -eq 1) {
        Assert-Equal -Expected 'GET' -Actual $version[0].method `
            -Message 'getCurrentVersionOfServer uses GET'
        Assert-Equal -Expected ('{0}/api/versions/current' -f $BasePath) -Actual $version[0].normalizedTarget `
            -Message 'getCurrentVersionOfServer target carries no query string'
        Assert-AuthenticatedRequest -Record $version[0] -Label 'getCurrentVersionOfServer'
    }
    if ($release.Count -eq 1) {
        Assert-Equal -Expected 'POST' -Actual $release[0].method `
            -Message 'releaseToken uses POST'
        Assert-Equal -Expected ('{0}/api/auth/token/release' -f $BasePath) -Actual $release[0].normalizedTarget `
            -Message 'releaseToken target carries no query string'
        Assert-AuthenticatedRequest -Record $release[0] -Label 'releaseToken'
    }
}

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
Write-Host 'VCF Operations precheck-gated onboarding -- contract verification'
Write-Host ('  contract: {0} @ {1}' -f $Contract.source.specPath, $Contract.source.repositoryCommitSha)

if (-not (Get-Module -ListAvailable -Name 'VMware.Sdk.Vcf.Ops')) {
    Write-Host 'FATAL: the prerequisite module VMware.Sdk.Vcf.Ops is not installed.'
    exit 2
}

$script:Scenario = 'implementation'
Assert-ImplementationContract

# =========================================================================
Write-Host ''
Write-Host 'Scenario: minimal onboarding, precheck passes'
$script:Scenario = 'minimal'
$run = Invoke-Scenario -ScenarioName 'minimal' -MockScenario 'precheck-pass'
$records = $run.Records

Assert-OnContract -Records $records
Assert-Sequence `
    -Expected @('acquireToken', 'getCurrentVersionOfServer', 'testConnection', 'createAdapterInstance', 'releaseToken') `
    -Actual @($records | Where-Object { $null -ne $_ } | ForEach-Object { $_.operationId }) `
    -Message 'the precheck is issued before the mutating call, and the session is released'
Assert-SessionRequests -Records $records

if ($records.Count -ge 1) { Assert-AcquireToken -Record $records[0] }

$precheck = Get-Record -Records $records -OperationId 'testConnection'
$create = Get-Record -Records $records -OperationId 'createAdapterInstance'

Assert-Equal -Expected 1 -Actual $precheck.Count -Message 'testConnection is called exactly once'
Assert-Equal -Expected 1 -Actual $create.Count -Message 'createAdapterInstance is called exactly once'

if ($precheck.Count -eq 1) {
    $r = $precheck[0]
    Assert-Equal -Expected 'POST' -Actual $r.method -Message 'testConnection uses POST'
    Assert-Equal -Expected ('{0}/api/adapters/testConnection' -f $BasePath) -Actual $r.normalizedTarget `
        -Message 'testConnection target carries no query string'
    Assert-AuthenticatedRequest -Record $r -Label 'testConnection'
    Assert-JsonRequest -Record $r -Label 'testConnection'
    Assert-NoEmptyMembers -Record $r -Label 'testConnection'

    $keys = @(Get-BodyKeys -Record $r | Sort-Object)
    Assert-Sequence -Expected @('adapterKindKey', 'name') -Actual $keys `
        -Message 'a minimal precheck body carries only the two required members, with description, collectorId, monitoringInterval and resourceIdentifiers absent'

    $body = Get-BodyObject -Record $r
    if ($body) {
        Assert-Equal -Expected 'VMWARE' -Actual $body.adapterKindKey -Message 'precheck body sends the requested adapter kind'
        Assert-Equal -Expected 'vc01 Adapter Instance' -Actual $body.name -Message 'precheck body sends the requested instance name'
    }
}

if ($create.Count -eq 1) {
    $r = $create[0]
    Assert-Equal -Expected 'POST' -Actual $r.method -Message 'createAdapterInstance uses POST'
    Assert-Equal -Expected ('{0}/api/adapters?force=false' -f $BasePath) -Actual $r.normalizedTarget `
        -Message 'createAdapterInstance sends force=false and omits the unset extractIdentifierDefaults'
    Assert-AuthenticatedRequest -Record $r -Label 'createAdapterInstance'
    Assert-JsonRequest -Record $r -Label 'createAdapterInstance'
    Assert-NoEmptyMembers -Record $r -Label 'createAdapterInstance'

    if ($precheck.Count -eq 1) {
        Assert-Equal -Expected $precheck[0].body -Actual $r.body `
            -Message 'the mutating call sends byte-identical payload to the one the precheck validated'
    }
}

$outcome = $run.Outcome
if ($null -eq $outcome) {
    Fail 'the scenario runner produced no result'
} else {
    Assert-True -Condition (-not $outcome.threw) -Message ('a successful onboarding does not throw ({0})' -f $outcome.errorMessage)
    Assert-Equal -Expected 1 -Actual $outcome.resultCount -Message 'exactly one result object is returned'
    Assert-ResultShape -Outcome $outcome
    Assert-Equal -Expected 'Created' -Actual $outcome.status -Message 'Status reports Created'
    Assert-Equal -Expected $true -Actual $outcome.precheckPassed -Message 'PrecheckPassed is true'
    Assert-Equal -Expected $CreatedId -Actual $outcome.adapterInstanceId -Message 'AdapterInstanceId is taken from the createAdapterInstance response'
}

# =========================================================================
Write-Host ''
Write-Host 'Scenario: fully specified onboarding, precheck passes'
$script:Scenario = 'full'
$run = Invoke-Scenario -ScenarioName 'full' -MockScenario 'precheck-pass'
$records = $run.Records

Assert-OnContract -Records $records
Assert-Sequence `
    -Expected @('acquireToken', 'getCurrentVersionOfServer', 'testConnection', 'createAdapterInstance', 'releaseToken') `
    -Actual @($records | Where-Object { $null -ne $_ } | ForEach-Object { $_.operationId }) `
    -Message 'the full onboarding also runs the precheck before create and releases the session'
Assert-SessionRequests -Records $records
$precheck = Get-Record -Records $records -OperationId 'testConnection'
$create = Get-Record -Records $records -OperationId 'createAdapterInstance'
Assert-Equal -Expected 1 -Actual $precheck.Count -Message 'testConnection is called exactly once'
Assert-Equal -Expected 1 -Actual $create.Count -Message 'createAdapterInstance is called exactly once'

if ($precheck.Count -eq 1) {
    $r = $precheck[0]
    Assert-Equal -Expected 'POST' -Actual $r.method -Message 'testConnection uses POST'
    Assert-Equal -Expected ('{0}/api/adapters/testConnection' -f $BasePath) -Actual $r.normalizedTarget `
        -Message 'testConnection target carries no query string'
    Assert-AuthenticatedRequest -Record $r -Label 'testConnection'
    Assert-JsonRequest -Record $r -Label 'testConnection'
    Assert-NoEmptyMembers -Record $r -Label 'testConnection'

    $keys = @(Get-BodyKeys -Record $r | Sort-Object)
    Assert-Sequence `
        -Expected @('adapterKindKey', 'collectorId', 'description', 'monitoringInterval', 'name', 'resourceIdentifiers') `
        -Actual $keys `
        -Message 'every supplied optional member reaches the wire'

    $body = Get-BodyObject -Record $r
    if ($body) {
        Assert-Equal -Expected 'VMWARE' -Actual $body.adapterKindKey -Message 'precheck body sends the requested adapter kind'
        Assert-Equal -Expected 'vc01 Adapter Instance' -Actual $body.name -Message 'precheck body sends the requested instance name'
        Assert-Equal -Expected 'Primary management vCenter' -Actual $body.description -Message 'description is sent verbatim'
        Assert-Equal -Expected '1' -Actual $body.collectorId -Message 'collectorId is sent as the specification string type'
        Assert-Equal -Expected 0 -Actual $body.monitoringInterval `
            -Message 'a supplied zero monitoringInterval is retained as a JSON number'

        $identifiers = @($body.resourceIdentifiers)
        Assert-Equal -Expected 3 -Actual $identifiers.Count -Message 'every resource identifier is sent'
        if ($identifiers.Count -eq 3) {
            Assert-Sequence -Expected @('VCURL', 'AUTODISCOVERY', 'PROCESSCHANGEEVENTS') `
                -Actual @($identifiers | ForEach-Object { $_.name }) `
                -Message 'resource identifiers keep the caller-supplied order'
            Assert-Sequence -Expected @('vc01.lab.example.com', 'true', 'false') `
                -Actual @($identifiers | ForEach-Object { $_.value }) `
                -Message 'resource identifier values are sent as name-value strings'
            $shape = @($identifiers | ForEach-Object { (@($_.PSObject.Properties.Name) | Sort-Object) -join '+' })
            Assert-Sequence -Expected @('name+value', 'name+value', 'name+value') -Actual $shape `
                -Message 'each resource identifier is a bare name-value pair'
        }
    }
}

if ($create.Count -eq 1) {
    $r = $create[0]
    Assert-Equal -Expected 'POST' -Actual $r.method -Message 'createAdapterInstance uses POST'
    Assert-Equal -Expected ('{0}/api/adapters?force=false' -f $BasePath) -Actual $r.normalizedTarget `
        -Message 'createAdapterInstance sends force=false and omits the unset extractIdentifierDefaults'
    Assert-AuthenticatedRequest -Record $r -Label 'createAdapterInstance'
    Assert-JsonRequest -Record $r -Label 'createAdapterInstance'
    Assert-NoEmptyMembers -Record $r -Label 'createAdapterInstance'
    if ($precheck.Count -eq 1) {
        Assert-Equal -Expected $precheck[0].body -Actual $r.body `
            -Message 'the mutating call sends byte-identical payload to the one the precheck validated'
    }
}

$outcome = $run.Outcome
if ($null -eq $outcome) {
    Fail 'the scenario runner produced no result'
} else {
    Assert-True -Condition (-not $outcome.threw) -Message ('a successful onboarding does not throw ({0})' -f $outcome.errorMessage)
    Assert-Equal -Expected 1 -Actual $outcome.resultCount -Message 'exactly one result object is returned'
    Assert-ResultShape -Outcome $outcome
    Assert-Equal -Expected 'Created' -Actual $outcome.status -Message 'Status reports Created'
    Assert-Equal -Expected $true -Actual $outcome.precheckPassed -Message 'PrecheckPassed is true'
    Assert-Equal -Expected $CreatedId -Actual $outcome.adapterInstanceId -Message 'AdapterInstanceId is taken from the createAdapterInstance response'
}

# =========================================================================
Write-Host ''
Write-Host 'Scenario: precheck fails, nothing may be changed'
$script:Scenario = 'precheck-fail'
$run = Invoke-Scenario -ScenarioName 'precheck-fail' -MockScenario 'precheck-fail'
$records = $run.Records

Assert-OnContract -Records $records
Assert-Sequence `
    -Expected @('acquireToken', 'getCurrentVersionOfServer', 'testConnection', 'releaseToken') `
    -Actual @($records | Where-Object { $null -ne $_ } | ForEach-Object { $_.operationId }) `
    -Message 'a failed precheck stops the workflow and still releases the session'
Assert-SessionRequests -Records $records

$create = Get-Record -Records $records -OperationId 'createAdapterInstance'
Assert-Equal -Expected 0 -Actual $create.Count -Message 'createAdapterInstance is never invoked after a failed precheck'

$mutating = @($records | Where-Object {
        $null -ne $_ -and $_.method -ne 'GET' -and $_.normalizedPath -eq ('{0}/api/adapters' -f $BasePath)
    })
Assert-Equal -Expected 0 -Actual $mutating.Count -Message 'no request whatsoever reaches the adapter-instance collection'

$precheck = Get-Record -Records $records -OperationId 'testConnection'
Assert-Equal -Expected 1 -Actual $precheck.Count -Message 'the precheck is attempted exactly once and not retried'
if ($precheck.Count -eq 1) {
    Assert-Equal -Expected 'POST' -Actual $precheck[0].method -Message 'testConnection uses POST'
    Assert-Equal -Expected ('{0}/api/adapters/testConnection' -f $BasePath) -Actual $precheck[0].normalizedTarget `
        -Message 'testConnection target carries no query string'
    Assert-AuthenticatedRequest -Record $precheck[0] -Label 'testConnection'
    Assert-JsonRequest -Record $precheck[0] -Label 'testConnection'
    Assert-NoEmptyMembers -Record $precheck[0] -Label 'testConnection'
    $keys = @(Get-BodyKeys -Record $precheck[0] | Sort-Object)
    Assert-Sequence -Expected @('adapterKindKey', 'name') -Actual $keys `
        -Message 'a minimal precheck body carries only the two required members'
}

$outcome = $run.Outcome
if ($null -eq $outcome) {
    Fail 'the scenario runner produced no result'
} else {
    Assert-True -Condition (-not $outcome.threw) `
        -Message ('a failed precheck is reported, not thrown ({0})' -f $outcome.errorMessage)
    Assert-Equal -Expected 1 -Actual $outcome.resultCount -Message 'exactly one result object is returned'
    Assert-ResultShape -Outcome $outcome
    Assert-Equal -Expected 'PrecheckFailed' -Actual $outcome.status -Message 'Status reports PrecheckFailed'
    Assert-Equal -Expected $false -Actual $outcome.precheckPassed -Message 'PrecheckPassed is false'
    Assert-True -Condition ([string]::IsNullOrEmpty([string]$outcome.adapterInstanceId)) `
        -Message 'AdapterInstanceId stays empty because nothing was created'
    Assert-True -Condition ([string]$outcome.message -match 'certificate presented by the endpoint is not trusted') `
        -Message 'the diagnostic returned by the precheck is surfaced in Message'
}

# ---------------------------------------------------------------------------
Write-Host ''
if ($script:Failures.Count -eq 0) {
    Write-Host ('PASS  {0} assertions' -f $script:Passed)
    exit 0
}
Write-Host ('FAIL  {0} of {1} assertions failed' -f $script:Failures.Count, ($script:Failures.Count + $script:Passed))
foreach ($failure in $script:Failures) { Write-Host ('  - {0}' -f $failure) }
exit 1
