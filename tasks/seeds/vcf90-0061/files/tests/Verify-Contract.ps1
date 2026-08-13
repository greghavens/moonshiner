#Requires -Version 7.2
<#
.SYNOPSIS
    Protected contract verification for VcfOpsActionRunner.

.DESCRIPTION
    Starts the loopback mock (tests/mock/Start-VcfOpsMock.ps1), connects the
    VMware.Sdk.Vcf.Ops client to it, drives Invoke-VcfOpsActionAndWait through
    four scenarios, and asserts the exact bytes that reached the wire by reading
    the mock's request log.

    No live VMware endpoint is contacted. The only network traffic is to
    127.0.0.1 on an ephemeral port.

    Exit code 0 means every assertion passed.
#>
[CmdletBinding()]
param(
    [switch]$Detailed
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# PowerCLI prints a CEIP notice on import that would bury the assertion output.
$WarningPreference = 'SilentlyContinue'

$repoRoot = Split-Path -Parent $PSScriptRoot
$contractPath = Join-Path $repoRoot 'docs/contract.json'
$fixturePath = Join-Path $PSScriptRoot 'mock/fixtures.json'
$mockScript = Join-Path $PSScriptRoot 'mock/Start-VcfOpsMock.ps1'
$modulePath = Join-Path $repoRoot 'src/VcfOpsActionRunner/VcfOpsActionRunner.psd1'
$artifactDir = Join-Path $PSScriptRoot '.artifacts'

$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
$fixtures = Get-Content -LiteralPath $fixturePath -Raw | ConvertFrom-Json

$null = New-Item -ItemType Directory -Path $artifactDir -Force
$logPath = Join-Path $artifactDir 'requests.jsonl'
$readyPath = Join-Path $artifactDir 'ready.txt'
Remove-Item -LiteralPath $logPath, $readyPath -Force -ErrorAction SilentlyContinue

# ---------------------------------------------------------------- assertions
$script:Failures = [System.Collections.Generic.List[string]]::new()
$script:Passed = 0
$script:Section = ''

function Set-Section { param([string]$Name) $script:Section = $Name; Write-Host "`n== $Name" -ForegroundColor Cyan }

function Assert-True {
    param([string]$Name, [bool]$Condition, [string]$Detail = '')
    if ($Condition) {
        $script:Passed++
        if ($Detailed) { Write-Host "   ok   $Name" -ForegroundColor DarkGray }
    }
    else {
        $msg = "[$script:Section] $Name"
        if ($Detail) { $msg += "`n         $Detail" }
        $script:Failures.Add($msg)
        Write-Host "   FAIL $Name" -ForegroundColor Red
        if ($Detail) { Write-Host "        $Detail" -ForegroundColor Red }
    }
}

function Assert-Equal {
    param([string]$Name, $Expected, $Actual)
    Assert-True -Name $Name -Condition ($Expected -eq $Actual) -Detail "expected <$Expected>, got <$Actual>"
}

function Test-SetEqual {
    param([string[]]$Actual, [string[]]$Expected)
    $a = @($Actual) | Sort-Object
    $b = @($Expected) | Sort-Object
    if (@($a).Count -ne @($b).Count) { return $false }
    if (@($a).Count -eq 0) { return $true }
    return -not (Compare-Object -ReferenceObject $a -DifferenceObject $b)
}

function Assert-KeySet {
    param([string]$Name, $Object, [string[]]$Expected)
    $actual = @($Object.PSObject.Properties.Name)
    Assert-True -Name $Name -Condition (Test-SetEqual -Actual $actual -Expected $Expected) `
        -Detail "expected keys {$($Expected -join ', ')}, got {$($actual -join ', ')}"
}

function Assert-PollSpacing {
    param([string]$Label, $Polls, [int]$MinimumMilliseconds = 900)

    $entries = @($Polls)
    for ($i = 1; $i -lt $entries.Count; $i++) {
        $gap = [long]$entries[$i].observedAtMilliseconds - [long]$entries[$i - 1].observedAtMilliseconds
        Assert-True "$Label`: poll $i to $($i + 1) waited for -PollIntervalSeconds" `
            ($gap -ge $MinimumMilliseconds) "observed gap=${gap}ms"
    }
}

# ------------------------------------------------------------- request log IO
# These emit their entries into the pipeline rather than returning an array, so
# every call site wraps them in @(). A function that returns an empty array
# hands back $null instead, and @($null) is a one-element array holding $null.
function Get-RequestLog {
    if (-not (Test-Path -LiteralPath $logPath)) { return }
    Get-Content -LiteralPath $logPath | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json }
}

function Get-LogSince {
    param([int]$Marker)
    Get-RequestLog | Where-Object { $_.seq -gt $Marker }
}

function Get-LogMarker {
    $all = @(Get-RequestLog)
    if ($all.Count -eq 0) { return 0 }
    $all[-1].seq
}

# Runs a scenario call and turns an unexpected terminating error into an ordinary
# failure, so one broken scenario does not hide the rest.
function Invoke-Guarded {
    param([string]$Name, [scriptblock]$Body)
    try {
        return (& $Body)
    }
    catch {
        Assert-True "$Name completed without an unexpected error" $false ($_ | Out-String)
        return $null
    }
}

function Get-Header {
    param($Entry, [string]$Name)
    $props = $Entry.headers.PSObject.Properties
    $hit = $props | Where-Object { $_.Name -eq $Name }
    if ($hit) { return $hit.Value }
    return $null
}

# ------------------------------------------------- shared wire-shape checks
$expectedToken = $contract.auth.valuePrefix + $fixtures.authToken.token

$moduleComposedOps = @(
    $contract.operations.PSObject.Properties.Name |
        Where-Object { $contract.operations.$_.composedByModuleUnderTest } |
        ForEach-Object { $contract.operations.$_.operationId }
)

# The HTTP exchange below proves the actual wire behavior. These source checks
# additionally enforce the task's explicit transport boundary: the module may
# use the installed generated SDK, but may not carry its own HTTP client or a
# vendored copy of that SDK.
Set-Section 'Source constraints - use the installed SDK as the only transport'
$sourceRoot = Join-Path $repoRoot 'src'
$sourceFiles = @(Get-ChildItem -LiteralPath $sourceRoot -Recurse -File)
$sourceCodeTokens = @(
    $sourceFiles | Where-Object Extension -in @('.ps1', '.psm1', '.psd1') | ForEach-Object {
        $tokens = $null
        $parseErrors = $null
        $null = [System.Management.Automation.Language.Parser]::ParseFile(
            $_.FullName, [ref]$tokens, [ref]$parseErrors)
        $tokens | Where-Object Kind -ne 'Comment' | ForEach-Object Text
    }
)
$sourceCode = $sourceCodeTokens -join ' '

$forbiddenTransport = '(?i)\bInvoke-(RestMethod|WebRequest)\b|\b(HttpClient|WebClient|HttpWebRequest|TcpClient|curl|wget)\b|System\.Net\.(Http|Sockets|WebRequest)|https?://|/suite-api/api/'
Assert-True 'source contains no direct HTTP transport or hand-built appliance URL' `
    ($sourceCode -notmatch $forbiddenTransport)

$vendoredSdk = @($sourceFiles | Where-Object {
    $_.Extension -in @('.dll', '.nupkg') -or $_.Name -match '(?i)^VMware\.(Sdk|Bindings)\.'
})
Assert-Equal 'src contains no vendored SDK binaries or modules' 0 $vendoredSdk.Count

$shadowedSdkCommands = '(?i)\bfunction\s+(Invoke-VcfOpsPerformAction|Invoke-VcfOpsGetActionStatus|Initialize-VcfOps(?:namevalue|actionparametergroup|actionexecution))\b'
Assert-True 'source does not replace generated SDK commands with local functions' `
    ($sourceCode -notmatch $shadowedSdkCommands)

function Assert-CommonRequestHygiene {
    param([string]$Label, $Entries)

    foreach ($e in @($Entries)) {
        if ($null -eq $e) { continue }
        if ($e.operationId -notin $moduleComposedOps) { continue }
        Assert-Equal "$Label`: $($e.method) $($e.path) carries the session token" $expectedToken (Get-Header $e 'authorization')
        $accept = Get-Header $e 'accept'
        Assert-True "$Label`: $($e.method) $($e.path) sends Accept: application/json" `
            ($accept -and $accept -match 'application/json') "accept=<$accept>"
        if ($e.method -eq 'POST') {
            $ct = Get-Header $e 'content-type'
            Assert-True "$Label`: POST $($e.path) sends Content-Type: application/json" `
                ($ct -and $ct -match '^application/json') "content-type=<$ct>"
            Assert-True "$Label`: POST $($e.path) body contains no null-valued field" `
                ($e.body -notmatch ':\s*null') "body=<$($e.body)>"
        }
    }
}

# ------------------------------------------------------------------ mock boot
$port = 0

$pwshPath = (Get-Process -Id $PID).Path
$mockProcess = Start-Process -FilePath $pwshPath -PassThru -NoNewWindow -ArgumentList @(
    '-NoProfile', '-NoLogo', '-NonInteractive', '-File', $mockScript,
    '-Port', $port, '-LogPath', $logPath, '-ReadyPath', $readyPath,
    '-ContractPath', $contractPath, '-FixturePath', $fixturePath
)

try {
    $deadline = [datetime]::UtcNow.AddSeconds(30)
    while (-not (Test-Path -LiteralPath $readyPath)) {
        if ($mockProcess.HasExited) { throw "Mock exited during start-up with code $($mockProcess.ExitCode)." }
        if ([datetime]::UtcNow -ge $deadline) { throw 'Mock did not become ready within 30s.' }
        Start-Sleep -Milliseconds 100
    }
    $port = [int](Get-Content -LiteralPath $readyPath -Raw)
    Write-Host "Contract mock listening on http://127.0.0.1:$port (log: $logPath)" -ForegroundColor DarkGray

    Import-Module VMware.Sdk.Vcf.Ops -ErrorAction Stop -WarningAction SilentlyContinue
    Import-Module $modulePath -Force -ErrorAction Stop -WarningAction SilentlyContinue

    $functionCommand = Get-Command -Name Invoke-VcfOpsActionAndWait -Module VcfOpsActionRunner -ErrorAction SilentlyContinue
    Assert-True 'Invoke-VcfOpsActionAndWait is exported' ([bool]$functionCommand)

    if ($functionCommand) {
        $expectedParameterTypes = [ordered]@{
            Server                = [object]
            ActionId              = [string]
            ResourceId            = [string[]]
            ContextId             = [string]
            ContextResourceId     = [string[]]
            Parameter             = [hashtable]
            IncludeDetail         = [switch]
            PollIntervalSeconds   = [int]
            TimeoutSeconds        = [int]
        }
        $declaredParameters = @($functionCommand.ScriptBlock.Ast.Body.ParamBlock.Parameters)
        $declaredNames = @($declaredParameters | ForEach-Object { $_.Name.VariablePath.UserPath })
        Assert-True 'public parameter names are unchanged' `
            (Test-SetEqual -Actual $declaredNames -Expected @($expectedParameterTypes.Keys)) `
            "expected <$($expectedParameterTypes.Keys -join ', ')>, got <$($declaredNames -join ', ')>"

        foreach ($entry in $expectedParameterTypes.GetEnumerator()) {
            Assert-Equal "parameter $($entry.Key) keeps its type" `
                $entry.Value.FullName $functionCommand.Parameters[$entry.Key].ParameterType.FullName
        }

        $pollParameter = $declaredParameters | Where-Object { $_.Name.VariablePath.UserPath -eq 'PollIntervalSeconds' }
        $timeoutParameter = $declaredParameters | Where-Object { $_.Name.VariablePath.UserPath -eq 'TimeoutSeconds' }
        Assert-Equal 'PollIntervalSeconds default remains 5' 5 $pollParameter.DefaultValue.SafeGetValue()
        Assert-Equal 'TimeoutSeconds default remains 900' 900 $timeoutParameter.DefaultValue.SafeGetValue()
    }

    $credential = [pscredential]::new('admin', (ConvertTo-SecureString 'mock-password' -AsPlainText -Force))
    $ops = Connect-VcfOpsServer -Server '127.0.0.1' -Port $port -Protocol 'http' `
        -Credential $credential -IgnoreInvalidCertificate -WarningAction SilentlyContinue

    $vm1 = '7e780215-da07-4da1-9167-cd6892dcfdd8'
    $vm2 = 'b3ff1c9e-51b0-4f19-9a0c-6d24b7f8e401'

    # ================================================================ minimal
    Set-Section 'Scenario 1 - minimal submission omits every unset optional'
    $marker = Get-LogMarker
    $r1 = Invoke-Guarded 'Scenario 1' {
        Invoke-VcfOpsActionAndWait -Server $ops -ActionId 'PowerOffVM' -ResourceId $vm1 `
            -PollIntervalSeconds 1 -TimeoutSeconds 30
    }
    $e1 = @(Get-LogSince $marker)
    Assert-CommonRequestHygiene 'S1' $e1

    $submits = @($e1 | Where-Object { $_.operationId -eq 'performAction' })
    Assert-Equal 'exactly one performAction request' 1 @($submits).Count
    if (@($submits).Count -eq 1) {
        $s = $submits[0]
        Assert-Equal 'performAction path' '/suite-api/api/actions/PowerOffVM' $s.path
        Assert-Equal 'performAction sends no query string' '' $s.query

        Assert-True 'raw body does not mention contextId' ($s.body -notmatch '"contextId"') "body=<$($s.body)>"
        Assert-True 'raw body does not mention contextResourceId' ($s.body -notmatch '"contextResourceId"') "body=<$($s.body)>"
        Assert-True 'raw body does not mention parameterValue' ($s.body -notmatch '"parameterValue"') "body=<$($s.body)>"

        $body = $s.body | ConvertFrom-Json
        Assert-KeySet 'action-execution carries only the required field' $body @('parameterGroup')
        Assert-Equal 'one parameter group' 1 @($body.parameterGroup).Count
        if (@($body.parameterGroup).Count -eq 1) {
            $pg = @($body.parameterGroup)[0]
            Assert-KeySet 'parameter group carries only the required field' $pg @('resourceId')
            Assert-Equal 'parameter group resourceId' $vm1 $pg.resourceId
        }
    }

    $expectedTask1 = $fixtures.actions.PowerOffVM.taskId
    $polls = @($e1 | Where-Object { $_.operationId -eq 'getActionStatus' })
    Assert-Equal 'RUNNING and an unrecognized state remain nonterminal (3 status calls)' 3 @($polls).Count
    Assert-PollSpacing 'Scenario 1' $polls
    foreach ($p in @($polls)) {
        Assert-Equal 'status poll targets the task id returned by performAction' "/suite-api/api/actions/$expectedTask1/status" $p.path
        Assert-Equal 'status poll omits the unset detail query parameter' '' $p.query
    }

    Assert-True 'Scenario 1 returned a result' ($null -ne $r1)
    if ($null -ne $r1) {
        Assert-True 'result is a single object' ($r1 -isnot [array])
        Assert-KeySet 'result has exactly the documented properties' $r1 `
            @('ActionId', 'TaskId', 'State', 'Succeeded', 'PollCount', 'Messages', 'Status')
        Assert-Equal 'result TaskId' $expectedTask1 $r1.TaskId
        Assert-Equal 'result ActionId' 'PowerOffVM' $r1.ActionId
        Assert-Equal 'result State' 'Completed' $r1.State
        Assert-Equal 'result Succeeded' $true $r1.Succeeded
        Assert-Equal 'result PollCount' 3 $r1.PollCount
        Assert-Equal 'result contains both appliance message strings' 2 @($r1.Messages).Count
        Assert-Equal 'first result message is a string from status' `
            'Powering off virtual machine' @($r1.Messages)[0]
        Assert-Equal 'second result message is a string from status' `
            'The virtual machine is powered Off' @($r1.Messages)[1]
        Assert-Equal 'Status is the terminal status object' 'Completed' $r1.Status.State
        Assert-Equal 'Status carries the returned task id' $expectedTask1 $r1.Status.TaskId
    }

    # ================================================================== full
    Set-Section 'Scenario 2 - every optional supplied is sent, detail=true is set'
    $marker = Get-LogMarker
    $r2 = Invoke-Guarded 'Scenario 2' {
        Invoke-VcfOpsActionAndWait -Server $ops -ActionId 'RebootGuest' -ResourceId @($vm1, $vm2) `
            -ContextId 'RebootGuest' -ContextResourceId @($vm1) `
            -Parameter @{ force = 'true'; timeoutSec = '120' } `
            -IncludeDetail -PollIntervalSeconds 1 -TimeoutSeconds 30
    }
    $e2 = @(Get-LogSince $marker)
    Assert-CommonRequestHygiene 'S2' $e2

    $submits2 = @($e2 | Where-Object { $_.operationId -eq 'performAction' })
    Assert-Equal 'exactly one performAction request' 1 @($submits2).Count
    if (@($submits2).Count -eq 1) {
        $body2 = $submits2[0].body | ConvertFrom-Json
        Assert-Equal 'performAction path' '/suite-api/api/actions/RebootGuest' $submits2[0].path
        Assert-KeySet 'action-execution carries all three supplied fields' $body2 @('contextId', 'contextResourceId', 'parameterGroup')
        Assert-Equal 'contextId' 'RebootGuest' $body2.contextId
        Assert-Equal 'contextResourceId length' 1 @($body2.contextResourceId).Count
        Assert-Equal 'contextResourceId[0]' $vm1 @($body2.contextResourceId)[0]
        Assert-Equal 'one parameter group per resource' 2 @($body2.parameterGroup).Count

        $groups = @($body2.parameterGroup)
        if ($groups.Count -eq 2) {
            Assert-Equal 'parameter group order follows -ResourceId' $vm1 $groups[0].resourceId
            Assert-Equal 'parameter group order follows -ResourceId' $vm2 $groups[1].resourceId
            foreach ($g in $groups) {
                Assert-KeySet 'parameter group carries resourceId and parameterValue' $g @('resourceId', 'parameterValue')
                Assert-Equal 'two name-value pairs' 2 @($g.parameterValue).Count
                foreach ($nv in @($g.parameterValue)) {
                    Assert-KeySet 'name-value carries exactly name and value' $nv @('name', 'value')
                }
                $map = @{}
                foreach ($nv in @($g.parameterValue)) { $map[$nv.name] = $nv.value }
                Assert-Equal 'parameter force' 'true' $map['force']
                Assert-Equal 'parameter timeoutSec' '120' $map['timeoutSec']
            }
        }
    }

    $expectedTask2 = $fixtures.actions.RebootGuest.taskId
    $polls2 = @($e2 | Where-Object { $_.operationId -eq 'getActionStatus' })
    Assert-Equal 'polled until terminal (2 status calls)' 2 @($polls2).Count
    Assert-PollSpacing 'Scenario 2' $polls2
    foreach ($p in @($polls2)) {
        Assert-Equal 'status poll path' "/suite-api/api/actions/$expectedTask2/status" $p.path
        Assert-Equal 'status poll sends detail=true' '?detail=true' $p.query
    }
    Assert-True 'Scenario 2 returned a result' ($null -ne $r2)
    if ($null -ne $r2) {
        Assert-Equal 'result State' 'COMPLETED_SUCCESSFULLY' $r2.State
        Assert-Equal 'result Succeeded' $true $r2.Succeeded
        Assert-Equal 'IncludeDetail returns per-object detail in Status' `
            1 @($r2.Status.ActionObjectStatuses).Count
    }

    # =============================================================== failure
    Set-Section 'Scenario 3 - terminal failure is reported, not thrown away'
    $marker = Get-LogMarker
    $r3 = Invoke-Guarded 'Scenario 3' {
        Invoke-VcfOpsActionAndWait -Server $ops -ActionId 'SetCpuCount' -ResourceId $vm1 `
            -PollIntervalSeconds 1 -TimeoutSeconds 30
    }
    $e3 = @(Get-LogSince $marker)
    Assert-CommonRequestHygiene 'S3' $e3

    $polls3 = @($e3 | Where-Object { $_.operationId -eq 'getActionStatus' })
    Assert-Equal 'INITIATED and RUNNING are not treated as terminal (3 status calls)' 3 @($polls3).Count
    Assert-PollSpacing 'Scenario 3' $polls3
    Assert-True 'a terminal failure is returned, not raised' ($null -ne $r3)
    if ($null -ne $r3) {
        Assert-Equal 'result State' 'FAILED' $r3.State
        Assert-Equal 'result Succeeded' $false $r3.Succeeded
        Assert-True 'appliance messages are surfaced' `
            (@($r3.Messages) -contains 'Cannot reconfigure CPU: another task is already in progress') `
            "messages=<$(@($r3.Messages) -join ' | ')>"
    }

    # =============================================================== timeout
    Set-Section 'Scenario 4 - a task that never settles times out'
    $marker = Get-LogMarker
    $threw = $false
    $errText = ''
    try {
        $null = Invoke-VcfOpsActionAndWait -Server $ops -ActionId 'DeleteUnusedSnapshots' -ResourceId $vm1 `
            -PollIntervalSeconds 1 -TimeoutSeconds 2
    }
    catch {
        $threw = $true
        $errText = $_.Exception.Message
    }
    $e4 = @(Get-LogSince $marker)
    Assert-CommonRequestHygiene 'S4' $e4

    Assert-True 'a task stuck in RUNNING raises a terminating error' $threw
    Assert-True 'the error names the timeout' ($errText -match '(?i)time(d)?\s*-?\s*out|timeout') "message=<$errText>"
    $polls4 = @($e4 | Where-Object { $_.operationId -eq 'getActionStatus' })
    Assert-True 'the task was polled more than once before giving up' (@($polls4).Count -ge 2) "polls=$(@($polls4).Count)"
    Assert-True 'polling did not hot-loop' (@($polls4).Count -le 6) "polls=$(@($polls4).Count)"
    Assert-PollSpacing 'Scenario 4' $polls4

    # ================================================================ global
    Set-Section 'Global - the module stayed inside the contract'
    $all = @(Get-RequestLog)
    $offContract = @($all | Where-Object { -not $_.servedByContract })
    Assert-Equal 'no request reached a path the contract does not name' 0 @($offContract).Count
    if (@($offContract).Count) {
        Write-Host ("        off-contract: " + (@($offContract | ForEach-Object { "$($_.method) $($_.path)" }) -join ', ')) -ForegroundColor Red
    }
    $nonOk = @($all | Where-Object { $_.statusCode -ne 200 })
    Assert-Equal 'every request was answered 200' 0 @($nonOk).Count
    if (@($nonOk).Count) {
        Write-Host ("        non-200: " + (@($nonOk | ForEach-Object { "$($_.statusCode) $($_.method) $($_.path)" }) -join ', ')) -ForegroundColor Red
    }

    $observed = @($all | ForEach-Object { $_.operationId } | Sort-Object -Unique)
    $declared = @($contract.operations.PSObject.Properties.Name | ForEach-Object { $contract.operations.$_.operationId })
    $extra = @($observed | Where-Object { $_ -notin $declared })
    Assert-Equal 'no operationId outside docs/contract.json was exercised' 0 @($extra).Count
    Assert-True 'the contract-declared async pair was actually used' `
        (('performAction' -in $observed) -and ('getActionStatus' -in $observed))
}
catch {
    # An unexpected terminating error is itself a verification failure. Record it
    # so the summary below is still printed rather than losing it to a stack trace.
    Set-Section 'Unhandled error'
    Assert-True 'verification ran to completion' $false ($_ | Out-String)
}
finally {
    # The mock is stopped without a releaseToken call on purpose: releaseToken is
    # not one of the operations this contract names, so the module must never
    # need it and the verifier must not issue it either.
    if ($mockProcess -and -not $mockProcess.HasExited) {
        Stop-Process -Id $mockProcess.Id -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ''
if ($script:Failures.Count -eq 0) {
    Write-Host "PASS - $script:Passed assertions" -ForegroundColor Green
    exit 0
}

Write-Host "FAIL - $($script:Failures.Count) failed, $script:Passed passed" -ForegroundColor Red
foreach ($f in $script:Failures) { Write-Host " - $f" -ForegroundColor Red }
exit 1
