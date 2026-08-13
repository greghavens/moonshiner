#Requires -Version 7.2
<#
    Protected verifier for the VcfVsanDataProtection module.

    Starts the contract-pinned loopback mock, drives the module through success,
    task-failure, timeout, validation-failure and HTTP-error scenarios, and
    asserts the exact request wire shape recorded in the mock's JSON Lines
    request log. No live VMware endpoint is contacted.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'

$root = Split-Path -Parent $PSScriptRoot
$contractPath = Join-Path $root 'docs/contract.json'
$mockPath = Join-Path $PSScriptRoot 'contract_mock.py'
$modulePath = Join-Path $root 'module/VcfVsanDataProtection/VcfVsanDataProtection.psd1'
$moduleSourcePath = Join-Path $root 'module/VcfVsanDataProtection/VcfVsanDataProtection.psm1'

$Username = 'administrator@vsphere.local'
$Password = 'VMw@re123!Snap'
$SessionToken = 'c9f1a4be6d0e47b8a2f35c7d1e0b9a64'
$Cluster = 'domain-c1013'
$CreateOp = 'Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task'

$script:Failures = [System.Collections.Generic.List[string]]::new()
$script:Checks = 0
$script:Connection = $null
$script:LogPath = $null

function Assert-That {
    param([bool] $Condition, [string] $Message)
    $script:Checks++
    if ($Condition) { Write-Host "  ok   $Message" }
    else {
        Write-Host "  FAIL $Message"
        $script:Failures.Add($Message)
    }
}

function Assert-Equal {
    param($Expected, $Actual, [string] $Message)
    # PowerShell's -eq coerces the right operand to the left operand's type,
    # which keeps Int32/Int64 comparisons of decoded JSON honest.
    $ok = if ($null -eq $Expected) { $null -eq $Actual } else { $Expected -eq $Actual }
    Assert-That ([bool]$ok) "$Message (expected '$Expected', got '$Actual')"
}

function Invoke-Guarded {
    param([string] $Name, [scriptblock] $Body)
    Write-Host "`n[$Name]"
    try { & $Body }
    catch { Assert-That $false "$Name raised an unexpected error: $($_.Exception.Message)" }
}

function Get-Prop {
    param($Object, [string] $Name)
    if ($null -eq $Object) { return $null }
    $prop = $Object.PSObject.Properties[$Name]
    if ($null -eq $prop) { return $null }
    return $prop.Value
}

function Get-JsonKeys {
    param($Object)
    if ($null -eq $Object) { return @() }
    return @($Object.PSObject.Properties.Name | Sort-Object)
}

function Read-Log {
    param([int] $From = 0)
    if (-not (Test-Path $script:LogPath)) { return @() }
    $lines = @(Get-Content $script:LogPath -ErrorAction SilentlyContinue | Where-Object { $_ })
    if ($lines.Count -le $From) { return @() }
    return @($lines[$From..($lines.Count - 1)] | ForEach-Object { $_ | ConvertFrom-Json })
}

function Get-LogCount {
    if (-not (Test-Path $script:LogPath)) { return 0 }
    return @(Get-Content $script:LogPath -ErrorAction SilentlyContinue | Where-Object { $_ }).Count
}

function Select-Op {
    param($Entries, [string] $OperationId)
    return @($Entries | Where-Object { (Get-Prop $_ 'operation_id') -eq $OperationId })
}

# --------------------------------------------------------------------------
# Start the contract-pinned mock
# --------------------------------------------------------------------------
$workDir = Join-Path ([System.IO.Path]::GetTempPath()) ("vsandp-verify-" + [guid]::NewGuid().ToString('n'))
New-Item -ItemType Directory -Path $workDir -Force | Out-Null
$script:LogPath = Join-Path $workDir 'requests.jsonl'
$portPath = Join-Path $workDir 'port'

$python = (Get-Command python3 -ErrorAction SilentlyContinue) ?? (Get-Command python -ErrorAction SilentlyContinue)
if ($null -eq $python) { throw 'python3 is required to run the contract mock.' }

$mock = Start-Process -FilePath $python.Source -PassThru -NoNewWindow `
    -ArgumentList @($mockPath, '--contract', $contractPath, '--log', $script:LogPath, '--port-file', $portPath) `
    -RedirectStandardError (Join-Path $workDir 'mock.err') `
    -RedirectStandardOutput (Join-Path $workDir 'mock.out')

try {
    $deadline = [datetime]::UtcNow.AddSeconds(30)
    while (-not (Test-Path $portPath) -and [datetime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 100
        if ($mock.HasExited) { throw "Contract mock exited: $(Get-Content (Join-Path $workDir 'mock.err') -Raw)" }
    }
    if (-not (Test-Path $portPath)) { throw 'Contract mock did not start in time.' }
    $port = [int](Get-Content $portPath -Raw).Trim()
    $baseUrl = "http://127.0.0.1:$port"
    Write-Host "Contract mock listening at $baseUrl"

    $credential = [pscredential]::new($Username, (ConvertTo-SecureString $Password -AsPlainText -Force))

    Invoke-Guarded 'module surface' {
        Import-Module $modulePath -Force -ErrorAction Stop
        $module = Get-Module VcfVsanDataProtection
        Assert-That ($null -ne $module) 'the VcfVsanDataProtection module imports'

        $exported = @($module.ExportedFunctions.Keys | Sort-Object)
        Assert-That (($exported -join ',') -eq 'Connect-VsanDpAppliance,New-VsanDpProtectionGroupSnapshot') `
            "exports exactly the two documented functions (got: $($exported -join ','))"

        $binding = [AppDomain]::CurrentDomain.GetAssemblies() |
            Where-Object { $_.GetName().Name -eq 'VMware.Binding.OpenApi' }
        Assert-That ($null -ne $binding) `
            'the VMware.Sdk.Vcf OpenAPI client runtime is loaded alongside the module'

        # The wire log proves request behavior. These AST checks separately
        # enforce the implementation constraint that those requests go through
        # the VMware OpenAPI runtime rather than a hand-rolled web cmdlet.
        $tokens = $null
        $parseErrors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile(
            $moduleSourcePath, [ref]$tokens, [ref]$parseErrors)
        Assert-Equal 0 @($parseErrors).Count 'the module source parses without errors'

        $typeNames = @($ast.FindAll({
            param($node)
            $node -is [System.Management.Automation.Language.TypeExpressionAst]
        }, $true) | ForEach-Object { $_.TypeName.FullName })
        foreach ($requiredType in @(
            'VMware.Binding.OpenApi.Client.ApiClient',
            'VMware.Binding.OpenApi.Client.RequestOptions',
            'VMware.Binding.OpenApi.Client.Configuration'
        )) {
            Assert-That ($requiredType -in $typeNames) "the implementation uses $requiredType"
        }

        $invokedMembers = @($ast.FindAll({
            param($node)
            $node -is [System.Management.Automation.Language.InvokeMemberExpressionAst]
        }, $true) | ForEach-Object { $_.Member.Extent.Text })
        foreach ($runtimeMethod in @('Post', 'Get')) {
            Assert-That ($runtimeMethod -in $invokedMembers) `
                "the implementation invokes the OpenAPI runtime $runtimeMethod method"
        }

        $commands = @($ast.FindAll({
            param($node)
            $node -is [System.Management.Automation.Language.CommandAst]
        }, $true) | ForEach-Object { $_.GetCommandName() } | Where-Object { $_ })
        $directWebCommands = @($commands | Where-Object {
            $_ -in @('Invoke-WebRequest', 'Invoke-RestMethod', 'curl', 'curl.exe', 'wget', 'wget.exe')
        })
        Assert-Equal 0 $directWebCommands.Count `
            "requests are not issued with direct web commands (found: $($directWebCommands -join ','))"
    }

    # ----------------------------------------------------------------------
    Invoke-Guarded 'Snapservice.Sessions_create' {
        $before = Get-LogCount
        $script:Connection = Connect-VsanDpAppliance -Server $baseUrl -Credential $credential -SkipCertificateCheck
        $entries = Read-Log -From $before

        Assert-Equal 1 $entries.Count 'session creation issues exactly one request'
        if ($entries.Count -ge 1) {
            $login = $entries[0]
            Assert-Equal 'Snapservice.Sessions_create' (Get-Prop $login 'operation_id') 'login matches the contract operation'
            Assert-Equal 'POST' (Get-Prop $login 'method') 'login uses POST'
            Assert-Equal '/api/snapservice/sessions' (Get-Prop $login 'path') 'login path is the contract path'
            Assert-Equal 201 (Get-Prop $login 'response_status') 'login is accepted with HTTP 201'

            $headers = Get-Prop $login 'headers'
            $expected = 'Basic ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${Username}:${Password}"))
            Assert-Equal $expected (Get-Prop $headers 'authorization') 'login sends HTTP Basic credentials'
            Assert-That ($null -eq (Get-Prop $headers 'vmware-api-session-id')) `
                'login does not send a session header it does not yet have'
            Assert-Equal 0 (Get-JsonKeys (Get-Prop $login 'query')).Count 'login sends no query parameters'
            Assert-Equal '' ([string](Get-Prop $login 'body')) 'login sends no request body'
        }
        Assert-Equal $SessionToken ([string](Get-Prop $script:Connection 'SessionId')) `
            'the connection carries the session token returned by the service'
        Assert-Equal $baseUrl ([string](Get-Prop $script:Connection 'Server')) `
            'the connection carries the server supplied by the caller'
        Assert-Equal "$baseUrl/api" ([string](Get-Prop $script:Connection 'BasePath')) `
            'the connection carries the service base path'
    }

    # ----------------------------------------------------------------------
    Invoke-Guarded 'snapshot without retention: unset optional field is omitted' {
        $before = Get-LogCount
        $result = New-VsanDpProtectionGroupSnapshot -Connection $script:Connection -Cluster $Cluster `
            -ProtectionGroup 'pg-2001' -Name 'nightly-2026-08-03' -PollIntervalSeconds 0 -TimeoutSeconds 60
        $entries = Read-Log -From $before
        $creates = Select-Op $entries $CreateOp
        $polls = Select-Op $entries 'Snapservice.Tasks_get'

        Assert-Equal 1 $creates.Count 'exactly one create request is issued'
        if ($creates.Count -ge 1) {
            $create = $creates[0]
            Assert-Equal 'POST' (Get-Prop $create 'method') 'create uses POST'
            Assert-Equal "/api/snapservice/clusters/$Cluster/protection-groups/pg-2001/snapshots" (Get-Prop $create 'path') `
                'create targets the contract path with both path parameters substituted'
            Assert-Equal 202 (Get-Prop $create 'response_status') 'create is accepted with HTTP 202'

            $query = Get-Prop $create 'query'
            Assert-Equal 'true' (Get-Prop $query 'vmw-task') 'create sends vmw-task=true'
            Assert-Equal 1 (Get-JsonKeys $query).Count 'create sends no query parameter beyond vmw-task'

            $headers = Get-Prop $create 'headers'
            Assert-Equal $SessionToken (Get-Prop $headers 'vmware-api-session-id') 'create authenticates with the session header'
            Assert-That ($null -eq (Get-Prop $headers 'authorization')) 'create does not resend Basic credentials'
            $mediaType = ([string](Get-Prop $headers 'content-type')).Split(';')[0].Trim().ToLowerInvariant()
            Assert-Equal 'application/json' $mediaType 'create sends a JSON request body'

            $rawBody = [string](Get-Prop $create 'body')
            $body = $rawBody | ConvertFrom-Json
            $keys = Get-JsonKeys $body
            Assert-That (($keys -join ',') -eq 'name') `
                "create body carries only the required property when retention is unset (got keys: $($keys -join ','))"
            Assert-Equal 'nightly-2026-08-03' (Get-Prop $body 'name') 'create body carries the requested snapshot name'
            Assert-That ($rawBody -notmatch '(?i)"retention"') `
                'the unset optional retention field is omitted rather than sent as null or an empty object'
        }

        Assert-Equal 4 $polls.Count 'the task is polled until it reports a terminal status, and no further'
        foreach ($poll in $polls) {
            Assert-Equal 'GET' (Get-Prop $poll 'method') 'each poll uses GET'
            Assert-Equal '/api/snapservice/tasks/task-9001' (Get-Prop $poll 'path') 'each poll targets the returned task'
            Assert-Equal $SessionToken (Get-Prop (Get-Prop $poll 'headers') 'vmware-api-session-id') `
                'each poll authenticates with the session header'
            Assert-That ($null -eq (Get-Prop (Get-Prop $poll 'headers') 'authorization')) `
                'polls do not resend Basic credentials'
            Assert-Equal 0 (Get-JsonKeys (Get-Prop $poll 'query')).Count 'polls send no query parameters'
            Assert-Equal '' ([string](Get-Prop $poll 'body')) 'polls send no request body'
        }
        if ($entries.Count -ge 1) {
            Assert-Equal 'Snapservice.Tasks_get' (Get-Prop $entries[$entries.Count - 1] 'operation_id') `
                'the last request of the run is the terminal poll'
        }

        Assert-Equal 'task-9001' ([string](Get-Prop $result 'TaskId')) 'the result reports the task identifier'
        Assert-Equal 'SUCCEEDED' ([string](Get-Prop $result 'Status')) 'the result reports the terminal status'
        Assert-Equal 'snap-4f0c1e77' ([string](Get-Prop $result 'Result')) 'the result carries the task result'
    }

    # ----------------------------------------------------------------------
    Invoke-Guarded 'snapshot with retention: nested required properties' {
        $before = Get-LogCount
        $retained = New-VsanDpProtectionGroupSnapshot -Connection $script:Connection -Cluster $Cluster `
            -ProtectionGroup 'pg-2002' -Name 'quarterly-keep' -RetentionDuration 7 -RetentionUnit 'DAY' `
            -PollIntervalSeconds 0 -TimeoutSeconds 60
        $entries = Read-Log -From $before
        $creates = Select-Op $entries $CreateOp
        $polls = Select-Op $entries 'Snapservice.Tasks_get'

        Assert-Equal 1 $creates.Count 'exactly one create request is issued'
        if ($creates.Count -ge 1) {
            $rawBody = [string](Get-Prop $creates[0] 'body')
            $body = $rawBody | ConvertFrom-Json
            $keys = Get-JsonKeys $body
            Assert-That (($keys -join ',') -eq 'name,retention') `
                "create body carries exactly name and retention (got keys: $($keys -join ','))"
            $retention = Get-Prop $body 'retention'
            $retentionKeys = Get-JsonKeys $retention
            Assert-That (($retentionKeys -join ',') -eq 'duration,unit') `
                "retention carries exactly its two required properties (got keys: $($retentionKeys -join ','))"
            Assert-Equal 'DAY' ([string](Get-Prop $retention 'unit')) 'retention unit is sent as the specification enum value'
            Assert-That ($rawBody -match '"duration"\s*:\s*7(?![0-9."])') `
                'retention duration is sent as a JSON number, not a quoted string'
            Assert-Equal 'true' (Get-Prop (Get-Prop $creates[0] 'query') 'vmw-task') `
                'the retained create sends vmw-task=true'
            Assert-Equal 1 (Get-JsonKeys (Get-Prop $creates[0] 'query')).Count `
                'the retained create sends no other query parameter'
            Assert-Equal $SessionToken (Get-Prop (Get-Prop $creates[0] 'headers') 'vmware-api-session-id') `
                'the retained create authenticates with the session header'
        }
        Assert-Equal 2 $polls.Count 'the task is polled to its terminal status'
        Assert-Equal 'SUCCEEDED' ([string](Get-Prop $retained 'Status')) 'the retained snapshot task succeeds'
        Assert-Equal 'snap-8b31d90a' ([string](Get-Prop $retained 'Result')) 'the retained snapshot result is returned'
    }

    # ----------------------------------------------------------------------
    Invoke-Guarded 'task reaches FAILED' {
        $before = Get-LogCount
        $threw = $false
        $message = ''
        try {
            New-VsanDpProtectionGroupSnapshot -Connection $script:Connection -Cluster $Cluster `
                -ProtectionGroup 'pg-2003' -Name 'doomed' -PollIntervalSeconds 0 -TimeoutSeconds 60 | Out-Null
        }
        catch {
            $threw = $true
            $message = [string]$_.Exception.Message
        }
        $entries = Read-Log -From $before
        $polls = Select-Op $entries 'Snapservice.Tasks_get'

        Assert-That $threw 'a task that reaches FAILED surfaces as a terminating error'
        Assert-That ($message -match '(?i)fail') 'the terminating error identifies the failure'
        Assert-That ($message -match 'Snapshot quiescing failed on a member virtual machine') `
            'the terminating error includes the message reported by the service'
        Assert-Equal 2 $polls.Count 'polling stops as soon as the task reports FAILED'
    }

    # ----------------------------------------------------------------------
    Invoke-Guarded 'invalid retention is rejected locally' {
        $cases = @(
            @{
                Name = 'duration without unit'
                Invoke = {
                    New-VsanDpProtectionGroupSnapshot -Connection $script:Connection -Cluster $Cluster `
                        -ProtectionGroup 'pg-2001' -Name 'half-retention' -RetentionDuration 5 `
                        -PollIntervalSeconds 0 -TimeoutSeconds 60 | Out-Null
                }
            },
            @{
                Name = 'unit without duration'
                Invoke = {
                    New-VsanDpProtectionGroupSnapshot -Connection $script:Connection -Cluster $Cluster `
                        -ProtectionGroup 'pg-2001' -Name 'half-retention' -RetentionUnit 'DAY' `
                        -PollIntervalSeconds 0 -TimeoutSeconds 60 | Out-Null
                }
            },
            @{
                Name = 'zero duration'
                Invoke = {
                    New-VsanDpProtectionGroupSnapshot -Connection $script:Connection -Cluster $Cluster `
                        -ProtectionGroup 'pg-2001' -Name 'zero-retention' -RetentionDuration 0 -RetentionUnit 'DAY' `
                        -PollIntervalSeconds 0 -TimeoutSeconds 60 | Out-Null
                }
            },
            @{
                Name = 'negative duration'
                Invoke = {
                    New-VsanDpProtectionGroupSnapshot -Connection $script:Connection -Cluster $Cluster `
                        -ProtectionGroup 'pg-2001' -Name 'negative-retention' -RetentionDuration -1 -RetentionUnit 'DAY' `
                        -PollIntervalSeconds 0 -TimeoutSeconds 60 | Out-Null
                }
            }
        )

        foreach ($case in $cases) {
            $before = Get-LogCount
            $threw = $false
            try { & $case.Invoke }
            catch { $threw = $true }
            Assert-That $threw "$($case.Name) is a terminating error"
            Assert-Equal 0 (Read-Log -From $before).Count `
                "$($case.Name) reaches the service as no request at all"
        }
    }

    # ----------------------------------------------------------------------
    Invoke-Guarded 'task polling times out' {
        $before = Get-LogCount
        $threw = $false
        try {
            New-VsanDpProtectionGroupSnapshot -Connection $script:Connection -Cluster $Cluster `
                -ProtectionGroup 'pg-2005' -Name 'never-finishes' -PollIntervalSeconds 1 -TimeoutSeconds 1 |
                Out-Null
        }
        catch { $threw = $true }
        $entries = Read-Log -From $before
        $polls = Select-Op $entries 'Snapservice.Tasks_get'

        Assert-That $threw 'a task that stays non-terminal past TimeoutSeconds raises a terminating error'
        Assert-Equal 1 $polls.Count 'no poll is issued after TimeoutSeconds elapses'
    }

    # ----------------------------------------------------------------------
    Invoke-Guarded 'whole-run invariants' {
        $all = Read-Log
        Assert-That ($all.Count -gt 0) 'the module actually contacted the contract mock'

        $unknown = @($all | Where-Object { (Get-Prop $_ 'unknown_route') -eq $true })
        Assert-Equal 0 $unknown.Count 'no request is made to a route the contract does not name'

        $unauthorized = @($all | Where-Object { (Get-Prop $_ 'response_status') -eq 401 })
        Assert-Equal 0 $unauthorized.Count 'no request is rejected as unauthenticated'

        $allowed = @('Snapservice.Sessions_create', $CreateOp, 'Snapservice.Tasks_get')
        $seen = @($all | ForEach-Object { Get-Prop $_ 'operation_id' } | Sort-Object -Unique)
        $extra = @($seen | Where-Object { $_ -notin $allowed })
        Assert-Equal 0 $extra.Count "only contract operations are called (unexpected: $($extra -join ','))"
    }

    # These expected error calls run after the whole-run success invariants so
    # their deliberate 401/404/503 responses cannot mask an authentication bug
    # in the successful workflows above.
    Invoke-Guarded 'non-success operation responses terminate' {
        $badCredential = [pscredential]::new(
            $Username, (ConvertTo-SecureString 'incorrect-password' -AsPlainText -Force))
        $before = Get-LogCount
        $sessionThrew = $false
        try {
            Connect-VsanDpAppliance -Server $baseUrl -Credential $badCredential -SkipCertificateCheck | Out-Null
        }
        catch { $sessionThrew = $true }
        $sessionEntries = Read-Log -From $before
        Assert-That $sessionThrew 'an HTTP error from session creation is terminating'
        Assert-Equal 1 $sessionEntries.Count 'the failed session attempt issues exactly one request'
        if ($sessionEntries.Count -eq 1) {
            Assert-Equal 401 (Get-Prop $sessionEntries[0] 'response_status') `
                'the session error case receives the mock HTTP 401'
        }

        $before = Get-LogCount
        $createThrew = $false
        try {
            New-VsanDpProtectionGroupSnapshot -Connection $script:Connection -Cluster 'domain-unknown' `
                -ProtectionGroup 'pg-2001' -Name 'missing-cluster' -PollIntervalSeconds 0 -TimeoutSeconds 60 |
                Out-Null
        }
        catch { $createThrew = $true }
        $createEntries = Read-Log -From $before
        Assert-That $createThrew 'an HTTP error from snapshot creation is terminating'
        Assert-Equal 1 $createEntries.Count 'a rejected create is not polled'
        if ($createEntries.Count -eq 1) {
            Assert-Equal 404 (Get-Prop $createEntries[0] 'response_status') `
                'the create error case receives the mock HTTP 404'
        }

        $before = Get-LogCount
        $pollThrew = $false
        try {
            New-VsanDpProtectionGroupSnapshot -Connection $script:Connection -Cluster $Cluster `
                -ProtectionGroup 'pg-2004' -Name 'poll-error' -PollIntervalSeconds 0 -TimeoutSeconds 60 |
                Out-Null
        }
        catch { $pollThrew = $true }
        $pollErrorEntries = Read-Log -From $before
        Assert-That $pollThrew 'an HTTP error while polling is terminating'
        Assert-Equal 1 (Select-Op $pollErrorEntries 'Snapservice.Tasks_get').Count `
            'polling stops on its first HTTP error'
        Assert-Equal 503 (Get-Prop $pollErrorEntries[$pollErrorEntries.Count - 1] 'response_status') `
            'the poll error case receives the mock HTTP 503'
    }
}
finally {
    if ($mock -and -not $mock.HasExited) {
        $mock.Kill()
        $mock.WaitForExit(5000) | Out-Null
    }
}

Write-Host ''
if ($script:Failures.Count -gt 0) {
    Write-Host "FAILED: $($script:Failures.Count) of $($script:Checks) checks did not pass."
    foreach ($failure in $script:Failures) { Write-Host "  - $failure" }
    exit 1
}
Write-Host "PASSED: all $($script:Checks) checks."
exit 0
