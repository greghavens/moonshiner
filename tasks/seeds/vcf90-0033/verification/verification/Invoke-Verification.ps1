#Requires -Version 7.2
<#
    Protected verifier for the VcfVcenterVmClone module.

    Starts the contract-pinned loopback mock, drives the module through the
    clone scenarios, and asserts the exact request wire shape recorded in the
    mock's JSON Lines request log. No live VMware endpoint is contacted.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'

$root = Split-Path -Parent $PSScriptRoot
$contractPath = Join-Path $root 'docs/contract.json'
$mockPath = Join-Path $PSScriptRoot 'contract_mock.py'
$modulePath = Join-Path $root 'module/VcfVcenterVmClone/VcfVcenterVmClone.psd1'

$Username = 'administrator@vsphere.local'
$Password = 'VMw@re123!Clone'
$SessionToken = 'b7d41f9e2c8a4051be36d7f0a91c5e28'
$CloneOp = 'Vcenter.VM_clone$Task'
$PollOp = 'Cis.Tasks_get'

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

function Assert-CloneEnvelope {
    param($Clone, [string] $Label)

    Assert-Equal 'POST' (Get-Prop $Clone 'method') "$Label uses POST"
    Assert-Equal '/api/vcenter/vm' (Get-Prop $Clone 'path') "$Label targets the contract path"
    Assert-Equal 202 (Get-Prop $Clone 'response_status') "$Label is accepted with HTTP 202"

    $query = Get-Prop $Clone 'query'
    Assert-Equal 'clone' (Get-Prop $query 'action') "$Label sends action=clone"
    Assert-Equal 'true' (Get-Prop $query 'vmw-task') "$Label sends vmw-task=true"
    Assert-Equal 2 (Get-JsonKeys $query).Count "$Label sends no query parameter beyond action and vmw-task"

    $headers = Get-Prop $Clone 'headers'
    Assert-Equal $SessionToken (Get-Prop $headers 'vmware-api-session-id') "$Label authenticates with the session header"
    Assert-That ($null -eq (Get-Prop $headers 'authorization')) "$Label does not resend Basic credentials"
    $mediaType = ([string](Get-Prop $headers 'content-type')).Split(';')[0].Trim().ToLowerInvariant()
    Assert-Equal 'application/json' $mediaType "$Label sends a JSON request body"
}

function Assert-PollShape {
    param($Polls, [string] $TaskId, [string] $Label)

    $escapedTaskId = [uri]::EscapeDataString($TaskId)
    foreach ($poll in $Polls) {
        Assert-Equal 'GET' (Get-Prop $poll 'method') "$Label poll uses GET"
        Assert-Equal "/api/cis/tasks/$escapedTaskId" (Get-Prop $poll 'path') "$Label poll targets the returned task"
        Assert-Equal $SessionToken (Get-Prop (Get-Prop $poll 'headers') 'vmware-api-session-id') `
            "$Label poll authenticates with the session header"
        Assert-Equal 0 (Get-JsonKeys (Get-Prop $poll 'query')).Count `
            "$Label poll omits the optional spec query parameter entirely"
        Assert-Equal '' ([string](Get-Prop $poll 'body')) "$Label poll sends no request body"
    }
}

# --------------------------------------------------------------------------
# Start the contract-pinned mock
# --------------------------------------------------------------------------
$workDir = Join-Path ([System.IO.Path]::GetTempPath()) ("vcvmclone-verify-" + [guid]::NewGuid().ToString('n'))
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
        $module = Get-Module VcfVcenterVmClone
        Assert-That ($null -ne $module) 'the VcfVcenterVmClone module imports'

        $exported = @($module.ExportedFunctions.Keys | Sort-Object)
        Assert-That (($exported -join ',') -eq 'Connect-VcfVcenterApi,New-VcfVcenterVmClone') `
            "exports exactly the two documented functions (got: $($exported -join ','))"

        $commonParameters = @(
            'Debug', 'ErrorAction', 'ErrorVariable', 'InformationAction', 'InformationVariable',
            'OutBuffer', 'OutVariable', 'PipelineVariable', 'ProgressAction', 'Verbose',
            'WarningAction', 'WarningVariable'
        )
        $connectParameters = @((Get-Command Connect-VcfVcenterApi).Parameters.Keys |
            Where-Object { $_ -notin $commonParameters } | Sort-Object)
        Assert-That (($connectParameters -join ',') -eq 'Credential,Server,SkipCertificateCheck') `
            "Connect-VcfVcenterApi preserves its public parameters (got: $($connectParameters -join ','))"

        $cloneParameters = @((Get-Command New-VcfVcenterVmClone).Parameters.Keys |
            Where-Object { $_ -notin $commonParameters } | Sort-Object)
        $expectedCloneParameters = @(
            'Cluster', 'Connection', 'Datastore', 'DisksToRemove', 'Folder',
            'GuestCustomizationName', 'HostSystem', 'Name', 'PollIntervalSeconds',
            'PowerOn', 'ResourcePool', 'SourceVm', 'TimeoutSeconds'
        )
        Assert-That (($cloneParameters -join ',') -eq ($expectedCloneParameters -join ',')) `
            "New-VcfVcenterVmClone preserves its public parameters (got: $($cloneParameters -join ','))"

        $binding = [AppDomain]::CurrentDomain.GetAssemblies() |
            Where-Object { $_.GetName().Name -eq 'VMware.Binding.OpenApi' }
        Assert-That ($null -ne $binding) `
            'the VMware.Sdk.Vcf OpenAPI client runtime is loaded alongside the module'
    }

    # ----------------------------------------------------------------------
    Invoke-Guarded 'Cis.Session_create' {
        $before = Get-LogCount
        $script:Connection = Connect-VcfVcenterApi -Server $baseUrl -Credential $credential -SkipCertificateCheck
        $entries = Read-Log -From $before

        Assert-Equal 1 $entries.Count 'session creation issues exactly one request'
        if ($entries.Count -ge 1) {
            $login = $entries[0]
            Assert-Equal 'Cis.Session_create' (Get-Prop $login 'operation_id') 'login matches the contract operation'
            Assert-Equal 'POST' (Get-Prop $login 'method') 'login uses POST'
            Assert-Equal '/api/session' (Get-Prop $login 'path') 'login path is the contract path'
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
    }

    # ----------------------------------------------------------------------
    Invoke-Guarded 'clone with only the required members: every unset optional is omitted' {
        $before = Get-LogCount
        $result = New-VcfVcenterVmClone -Connection $script:Connection -SourceVm 'vm-101' `
            -Name 'app-tier-clone-01' -PollIntervalSeconds 0 -TimeoutSeconds 60
        $entries = Read-Log -From $before
        $clones = Select-Op $entries $CloneOp
        $polls = Select-Op $entries $PollOp

        Assert-Equal 1 $clones.Count 'exactly one clone request is issued'
        if ($clones.Count -ge 1) {
            $clone = $clones[0]
            Assert-CloneEnvelope $clone 'clone'

            $rawBody = [string](Get-Prop $clone 'body')
            $body = $rawBody | ConvertFrom-Json
            $keys = Get-JsonKeys $body
            Assert-That (($keys -join ',') -eq 'name,source') `
                "clone body carries only the two required properties (got keys: $($keys -join ','))"
            Assert-Equal 'app-tier-clone-01' (Get-Prop $body 'name') 'clone body carries the requested virtual machine name'
            Assert-Equal 'vm-101' (Get-Prop $body 'source') 'clone body carries the source virtual machine identifier'

            foreach ($optional in @('placement', 'disks_to_remove', 'disks_to_update', 'power_on', 'guest_customization_spec')) {
                Assert-That ($rawBody -notmatch ('(?i)"' + [regex]::Escape($optional) + '"')) `
                    "the unset optional $optional is omitted rather than sent as null, false or an empty value"
            }
        }

        Assert-Equal 4 $polls.Count 'the task is polled until it reports a terminal status, and no further'
        Assert-PollShape $polls 'task-5001' 'clone'
        if ($entries.Count -ge 1) {
            Assert-Equal $PollOp (Get-Prop $entries[$entries.Count - 1] 'operation_id') `
                'the last request of the run is the terminal poll'
        }

        Assert-Equal 'task-5001' ([string](Get-Prop $result 'TaskId')) 'the result reports the task identifier'
        Assert-Equal 'SUCCEEDED' ([string](Get-Prop $result 'Status')) 'the result reports the terminal status'
        Assert-Equal 'vm-2087' ([string](Get-Prop $result 'Result')) 'the result carries the cloned virtual machine identifier'
    }

    # ----------------------------------------------------------------------
    Invoke-Guarded 'clone with optional members: only the supplied ones are sent' {
        $before = Get-LogCount
        $placed = New-VcfVcenterVmClone -Connection $script:Connection -SourceVm 'vm-102' `
            -Name 'db-tier-clone-01' -Folder 'group-v1022' -Datastore 'datastore-1031' `
            -PowerOn -DisksToRemove @('2000', '2001') -GuestCustomizationName 'linux-prep' `
            -PollIntervalSeconds 0 -TimeoutSeconds 60
        $entries = Read-Log -From $before
        $clones = Select-Op $entries $CloneOp
        $polls = Select-Op $entries $PollOp

        Assert-Equal 1 $clones.Count 'exactly one clone request is issued'
        if ($clones.Count -ge 1) {
            $rawBody = [string](Get-Prop $clones[0] 'body')
            $body = $rawBody | ConvertFrom-Json
            $keys = Get-JsonKeys $body
            Assert-That (($keys -join ',') -eq 'disks_to_remove,guest_customization_spec,name,placement,power_on,source') `
                "clone body carries exactly the required and supplied optional properties (got keys: $($keys -join ','))"

            $placement = Get-Prop $body 'placement'
            $placementKeys = Get-JsonKeys $placement
            Assert-That (($placementKeys -join ',') -eq 'datastore,folder') `
                "placement carries only the supplied members (got keys: $($placementKeys -join ','))"
            Assert-Equal 'group-v1022' ([string](Get-Prop $placement 'folder')) 'placement folder is the supplied identifier'
            Assert-Equal 'datastore-1031' ([string](Get-Prop $placement 'datastore')) 'placement datastore is the supplied identifier'
            foreach ($unset in @('resource_pool', 'host', 'cluster')) {
                Assert-That ($rawBody -notmatch ('(?i)"' + $unset + '"')) `
                    "the unsupplied placement member $unset is omitted rather than sent empty"
            }

            Assert-That ($rawBody -match '"power_on"\s*:\s*true') 'power_on is sent as a JSON boolean, not a quoted string'

            $disks = @(Get-Prop $body 'disks_to_remove')
            Assert-Equal 2 $disks.Count 'disks_to_remove carries both supplied disk identifiers'
            Assert-That ($rawBody -match '"disks_to_remove"\s*:\s*\[\s*"2000"\s*,\s*"2001"\s*\]') `
                'disks_to_remove is sent as a JSON array of identifier strings'

            $guest = Get-Prop $body 'guest_customization_spec'
            $guestKeys = Get-JsonKeys $guest
            Assert-That (($guestKeys -join ',') -eq 'name') `
                "guest_customization_spec carries only its single member (got keys: $($guestKeys -join ','))"
            Assert-Equal 'linux-prep' ([string](Get-Prop $guest 'name')) 'guest_customization_spec name is the supplied specification'

            Assert-That ($rawBody -notmatch '(?i)"disks_to_update"') 'the unset optional disks_to_update is omitted'
        }

        Assert-Equal 2 $polls.Count 'the task is polled to its terminal status'
        Assert-PollShape $polls 'task-5002' 'placed clone'
        Assert-Equal 'SUCCEEDED' ([string](Get-Prop $placed 'Status')) 'the placed clone task succeeds'
        Assert-Equal 'vm-2088' ([string](Get-Prop $placed 'Result')) 'the placed clone result is returned'
    }

    # ----------------------------------------------------------------------
    Invoke-Guarded 'an explicit -PowerOn:$false is a value, not an omission' {
        $before = Get-LogCount
        New-VcfVcenterVmClone -Connection $script:Connection -SourceVm 'vm-102' -Name 'db-tier-clone-02' `
            -PowerOn:$false -PollIntervalSeconds 0 -TimeoutSeconds 60 | Out-Null
        $entries = Read-Log -From $before
        $clones = Select-Op $entries $CloneOp

        Assert-Equal 1 $clones.Count 'exactly one clone request is issued'
        if ($clones.Count -ge 1) {
            $rawBody = [string](Get-Prop $clones[0] 'body')
            $body = $rawBody | ConvertFrom-Json
            $keys = Get-JsonKeys $body
            Assert-That (($keys -join ',') -eq 'name,power_on,source') `
                "an explicitly supplied false is preserved in the body (got keys: $($keys -join ','))"
            Assert-That ($rawBody -match '"power_on"\s*:\s*false') 'power_on is sent as JSON false'
        }
    }

    # ----------------------------------------------------------------------
    Invoke-Guarded 'all placement mappings and an escaped task identifier' {
        $before = Get-LogCount
        $escaped = New-VcfVcenterVmClone -Connection $script:Connection -SourceVm 'vm-104' `
            -Name 'edge-clone' -ResourcePool 'resgroup-42' -HostSystem 'host-42' -Cluster 'domain-c42' `
            -PollIntervalSeconds 0 -TimeoutSeconds 60
        $entries = Read-Log -From $before
        $clones = Select-Op $entries $CloneOp
        $polls = Select-Op $entries $PollOp

        Assert-Equal 1 $clones.Count 'the mapping case issues exactly one clone request'
        if ($clones.Count -ge 1) {
            Assert-CloneEnvelope $clones[0] 'mapping clone'
            $body = ([string](Get-Prop $clones[0] 'body')) | ConvertFrom-Json
            $placement = Get-Prop $body 'placement'
            $placementKeys = Get-JsonKeys $placement
            Assert-That (($placementKeys -join ',') -eq 'cluster,host,resource_pool') `
                "the remaining placement switches map to exactly their contract members (got keys: $($placementKeys -join ','))"
            Assert-Equal 'resgroup-42' ([string](Get-Prop $placement 'resource_pool')) `
                'ResourcePool maps to placement.resource_pool'
            Assert-Equal 'host-42' ([string](Get-Prop $placement 'host')) `
                'HostSystem maps to placement.host'
            Assert-Equal 'domain-c42' ([string](Get-Prop $placement 'cluster')) `
                'Cluster maps to placement.cluster'
        }

        Assert-Equal 1 $polls.Count 'a task that immediately succeeds is still polled exactly once'
        Assert-PollShape $polls 'task 5004/blue%canary' 'escaped identifier'
        Assert-Equal 'task 5004/blue%canary' ([string](Get-Prop $escaped 'TaskId')) `
            'the logical task identifier is returned without URL encoding'
        Assert-Equal 'vm-2089' ([string](Get-Prop $escaped 'Result')) `
            'the escaped-identifier task result is returned'
    }

    # ----------------------------------------------------------------------
    Invoke-Guarded 'exact operation success statuses are enforced' {
        $statusCredential = [pscredential]::new(
            'wrong-status@vsphere.local',
            (ConvertTo-SecureString 'ValidButWrongStatus!' -AsPlainText -Force))
        $before = Get-LogCount
        $loginThrew = $false
        try {
            Connect-VcfVcenterApi -Server $baseUrl -Credential $statusCredential | Out-Null
        }
        catch { $loginThrew = $true }
        $loginEntries = Read-Log -From $before
        Assert-That $loginThrew 'HTTP 200 is rejected where Cis.Session_create requires HTTP 201'
        Assert-Equal 1 $loginEntries.Count 'the wrong session status does not trigger a retry'
        if ($loginEntries.Count -ge 1) {
            Assert-Equal 'Cis.Session_create' (Get-Prop $loginEntries[0] 'operation_id') `
                'the wrong-status probe still calls only the session operation'
            Assert-Equal 200 (Get-Prop $loginEntries[0] 'response_status') `
                'the session status probe receives the deliberately wrong HTTP 200'
        }

        $before = Get-LogCount
        $cloneThrew = $false
        try {
            New-VcfVcenterVmClone -Connection $script:Connection -SourceVm 'vm-105' -Name 'wrong-clone-status' `
                -PollIntervalSeconds 0 -TimeoutSeconds 60 | Out-Null
        }
        catch { $cloneThrew = $true }
        $cloneEntries = Read-Log -From $before
        Assert-That $cloneThrew 'HTTP 200 is rejected where Vcenter.VM_clone$Task requires HTTP 202'
        Assert-Equal 1 (Select-Op $cloneEntries $CloneOp).Count 'the wrong clone status does not trigger a retry'
        Assert-Equal 0 (Select-Op $cloneEntries $PollOp).Count 'a task from the wrong clone status is never polled'

        $before = Get-LogCount
        $pollThrew = $false
        $pollMessage = ''
        try {
            New-VcfVcenterVmClone -Connection $script:Connection -SourceVm 'vm-106' -Name 'wrong-poll-status' `
                -PollIntervalSeconds 0 -TimeoutSeconds 60 | Out-Null
        }
        catch {
            $pollThrew = $true
            $pollMessage = [string]$_.Exception.Message
        }
        $pollEntries = Read-Log -From $before
        Assert-That $pollThrew 'HTTP 201 is rejected where Cis.Tasks_get requires HTTP 200'
        Assert-Equal 1 (Select-Op $pollEntries $PollOp).Count 'polling stops at the first wrong HTTP status'
        Assert-That ($pollMessage -notmatch [regex]::Escape($SessionToken)) `
            'a poll error does not expose the session token'
    }

    # ----------------------------------------------------------------------
    Invoke-Guarded 'timeout before a terminal status' {
        $before = Get-LogCount
        $threw = $false
        $message = ''
        try {
            New-VcfVcenterVmClone -Connection $script:Connection -SourceVm 'vm-107' -Name 'slow-clone' `
                -PollIntervalSeconds 0 -TimeoutSeconds 1 | Out-Null
        }
        catch {
            $threw = $true
            $message = [string]$_.Exception.Message
        }
        $entries = Read-Log -From $before
        Assert-That $threw 'a clone that misses its deadline raises a terminating error'
        Assert-That ($message -match '(?i)time|second|terminal') 'the timeout error identifies the deadline failure'
        Assert-Equal 1 (Select-Op $entries $PollOp).Count `
            'polling stops after the response that establishes the deadline was exceeded'
        Assert-That ($message -notmatch [regex]::Escape($SessionToken)) `
            'the timeout error does not expose the session token'
    }

    # ----------------------------------------------------------------------
    Invoke-Guarded 'task reaches FAILED' {
        $before = Get-LogCount
        $threw = $false
        $message = ''
        try {
            New-VcfVcenterVmClone -Connection $script:Connection -SourceVm 'vm-103' -Name 'doomed-clone' `
                -PollIntervalSeconds 0 -TimeoutSeconds 60 | Out-Null
        }
        catch {
            $threw = $true
            $message = [string]$_.Exception.Message
        }
        $entries = Read-Log -From $before
        $polls = Select-Op $entries $PollOp

        Assert-That $threw 'a task that reaches FAILED surfaces as a terminating error'
        Assert-That ($message -match '(?i)insufficient free space') `
            'the terminating error carries the service failure text'
        Assert-That ($message -notmatch [regex]::Escape($SessionToken)) `
            'the task failure text does not expose the session token'
        Assert-Equal 2 $polls.Count 'polling stops as soon as the task reports FAILED'
    }

    # ----------------------------------------------------------------------
    Invoke-Guarded 'connection errors do not expose passwords' {
        $badPassword = 'Wrong-Password-For-Redaction!'
        $badCredential = [pscredential]::new(
            $Username, (ConvertTo-SecureString $badPassword -AsPlainText -Force))
        $badAuthorization = 'Basic ' + [Convert]::ToBase64String(
            [Text.Encoding]::UTF8.GetBytes("${Username}:${badPassword}"))
        $before = Get-LogCount
        $threw = $false
        $message = ''
        try {
            Connect-VcfVcenterApi -Server $baseUrl -Credential $badCredential | Out-Null
        }
        catch {
            $threw = $true
            $message = [string]$_.Exception.Message
        }
        $entries = Read-Log -From $before
        Assert-That $threw 'an unauthenticated session attempt raises a terminating error'
        Assert-Equal 1 $entries.Count 'the failed connection attempt is not retried'
        if ($entries.Count -ge 1) {
            Assert-Equal 'Cis.Session_create' (Get-Prop $entries[0] 'operation_id') `
                'the failed connection still uses the session operation'
            Assert-Equal 401 (Get-Prop $entries[0] 'response_status') `
                'the bad credentials are rejected as unauthenticated'
        }
        Assert-That ($message -notmatch [regex]::Escape($badPassword)) `
            'the connection error does not expose the password'
        Assert-That ($message -notmatch [regex]::Escape($badAuthorization)) `
            'the connection error does not expose the encoded credentials'
    }

    # ----------------------------------------------------------------------
    Invoke-Guarded 'whole-run invariants' {
        $all = Read-Log
        Assert-That ($all.Count -gt 0) 'the module actually contacted the contract mock'

        $unknown = @($all | Where-Object { (Get-Prop $_ 'unknown_route') -eq $true })
        Assert-Equal 0 $unknown.Count 'no request is made to a route the contract does not name'

        $unauthorized = @($all | Where-Object { (Get-Prop $_ 'response_status') -eq 401 })
        Assert-Equal 1 $unauthorized.Count 'only the intentional invalid-login probe is unauthenticated'

        $badRequests = @($all | Where-Object { (Get-Prop $_ 'response_status') -eq 400 })
        Assert-Equal 0 $badRequests.Count 'no request is rejected as malformed'

        $logins = Select-Op $all 'Cis.Session_create'
        $createdSessions = @($logins | Where-Object { (Get-Prop $_ 'response_status') -eq 201 })
        Assert-Equal 1 $createdSessions.Count 'the session is created once and reused for every later request'

        $later = @($all | Where-Object { (Get-Prop $_ 'operation_id') -ne 'Cis.Session_create' })
        $badSessionHeaders = @($later | Where-Object {
            (Get-Prop (Get-Prop $_ 'headers') 'vmware-api-session-id') -ne $SessionToken
        })
        Assert-Equal 0 $badSessionHeaders.Count 'every post-login request reuses the created session token'
        $resentBasic = @($later | Where-Object {
            $null -ne (Get-Prop (Get-Prop $_ 'headers') 'authorization')
        })
        Assert-Equal 0 $resentBasic.Count 'no post-login request resends Basic credentials'

        $allowed = @('Cis.Session_create', $CloneOp, $PollOp)
        $seen = @($all | ForEach-Object { Get-Prop $_ 'operation_id' } | Sort-Object -Unique)
        $extra = @($seen | Where-Object { $_ -notin $allowed })
        Assert-Equal 0 $extra.Count "only contract operations are called (unexpected: $($extra -join ','))"
    }
}
finally {
    if ($mock -and -not $mock.HasExited) {
        $mock.Kill()
        $mock.WaitForExit(5000) | Out-Null
    }
    if (Test-Path $workDir) {
        Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
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
