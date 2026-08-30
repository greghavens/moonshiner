<#
.SYNOPSIS
    Loopback mock of the VCF SDDC LCM service, pinned to docs/contract.json.

.DESCRIPTION
    Serves ONLY the four operations named in docs/contract.json. Route templates,
    HTTP methods, base path and the bearer-auth requirement are all read from the
    contract at startup rather than hard-coded, so the mock cannot drift from the
    specification-derived contract.

    Anything that is not an exact (method, path-template) match from the contract
    is answered 404 and recorded in the request log with "matched": false, so a
    test can assert that the client never went off-contract.

    Every request is appended to -LogPath as one JSON object per line, capturing
    the RAW query string and the RAW request body. Wire-shape assertions depend on
    those being unparsed: '{}' and '{"lookBackWindow":null}' must stay
    distinguishable.

    IMPORTANT: the POST operation is deliberately NOT idempotent. Each call to
    generateComponentSupportBundle mints a brand new task and a brand new support
    bundle, exactly as the real service would. Any de-duplication has to come from
    the client.

    Binds 127.0.0.1 only. Never contacts a VMware endpoint.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][int]    $Port,
    [Parameter(Mandatory)][string] $LogPath,
    [Parameter(Mandatory)][string] $ContractPath,
    [string] $ReadyPath,
    [string] $Token = 'mock-sddc-lcm-token',

    # Number of getTask polls a task stays RUNNING before flipping to SUCCEEDED.
    [int] $PollsBeforeSuccess = 1
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$contract = Get-Content -LiteralPath $ContractPath -Raw | ConvertFrom-Json

# ---------------------------------------------------------------- route table
# Build the route table from the contract so the mock serves the contract and
# nothing else.
$routes = foreach ($name in $contract.operations.PSObject.Properties.Name) {
    $op = $contract.operations.$name
    # '/sddc-lcm/v1/components/{componentId}/support-bundles' -> anchored regex.
    # [regex]::Escape escapes '{' but leaves '}' bare, so tolerate either form.
    $pattern = '^' + ([regex]::Escape($op.path) -replace '\\?\{(\w+)\\?\}', '(?<$1>[^/]+)') + '$'
    [pscustomobject]@{
        OperationId = $op.operationId
        Method      = $op.method
        Pattern     = $pattern
    }
}

# ------------------------------------------------------------ deterministic ids
$script:Seq = 0
function New-DeterministicGuid {
    param([string]$Prefix)
    $script:Seq++
    # Stable, valid v4-shaped UUID so reruns produce identical logs.
    '{0}-0000-4000-8000-{1}' -f $Prefix, $script:Seq.ToString('000000000000')
}

$script:BaseTime = [datetime]::Parse('2026-01-15T09:00:00Z').ToUniversalTime()
$script:TimeStep = 0
function New-Timestamp {
    $script:TimeStep++
    $script:BaseTime.AddSeconds($script:TimeStep).ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
}

# ------------------------------------------------------------------- mock state
$script:Tasks     = [ordered]@{}   # taskId -> task object
$script:Bundles   = [ordered]@{}   # bundleId -> support bundle (materialised)
$script:Polls     = @{}            # taskId -> poll count

function New-ErrorResponse {
    param([string]$Code, [string]$Message)
    [ordered]@{
        code       = $Code
        message    = [ordered]@{ id = "com.broadcom.lcm.mock.$Code"; defaultMessage = $Message; localizedMessage = $Message; args = @{} }
        resolution = [ordered]@{ id = "com.broadcom.lcm.mock.$Code.resolution"; defaultMessage = 'See the SDDC LCM contract.'; localizedMessage = 'See the SDDC LCM contract.'; args = @{} }
        referenceId = New-DeterministicGuid -Prefix 'e0000000'
        timestamp   = New-Timestamp
    }
}

function ConvertTo-TaskSummary {
    param($Task)
    # TaskSummary is Task minus stages/subTasks/messages/result/additionalDetails.
    $s = [ordered]@{}
    foreach ($k in 'id','name','description','status','type','createdBy','updatedBy',
                   'resourceId','resourceType','createTime','startTime','updateTime',
                   'endTime','correlationId','retriable','cancellable') {
        if ($Task.Contains($k)) { $s[$k] = $Task[$k] }
    }
    $s
}

# ------------------------------------------------------------------- listener
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://127.0.0.1:$Port/")
$listener.Start()

# Truncate the log for a clean run.
Set-Content -LiteralPath $LogPath -Value '' -NoNewline
if ($ReadyPath) { Set-Content -LiteralPath $ReadyPath -Value $Port }

$authHeader   = $contract.security.header          # Authorization
$authExpected = $contract.security.valuePrefix + $Token

try {
    while ($listener.IsListening) {
        $ctx = $listener.GetContext()
        $req = $ctx.Request
        $res = $ctx.Response

        $rawPath  = $req.Url.AbsolutePath
        $rawQuery = $req.Url.Query           # includes leading '?', '' when absent

        $body = ''
        if ($req.HasEntityBody) {
            $reader = [System.IO.StreamReader]::new($req.InputStream, $req.ContentEncoding)
            $body = $reader.ReadToEnd()
            $reader.Close()
        }

        $headers = [ordered]@{}
        foreach ($h in $req.Headers.AllKeys) { $headers[$h] = $req.Headers[$h] }

        # Match against the contract-derived route table.
        $matchedRoute = $null
        $routeValues  = @{}
        foreach ($r in $routes) {
            if ($req.HttpMethod -ne $r.Method) { continue }
            $m = [regex]::Match($rawPath, $r.Pattern)
            if ($m.Success) {
                $matchedRoute = $r
                foreach ($g in ([regex]$r.Pattern).GetGroupNames()) {
                    if ($g -notmatch '^\d+$') { $routeValues[$g] = $m.Groups[$g].Value }
                }
                break
            }
        }

        $status = 200
        $payload = $null

        if (-not $matchedRoute) {
            $status = 404
            $payload = New-ErrorResponse -Code 'OFF_CONTRACT' `
                -Message "No operation in docs/contract.json serves $($req.HttpMethod) $rawPath."
        }
        elseif ($req.Headers[$authHeader] -ne $authExpected) {
            $status = 401
            $payload = New-ErrorResponse -Code 'UNAUTHORIZED' `
                -Message "Missing or invalid $authHeader header; expected the bearer token."
        }
        else {
            switch ($matchedRoute.OperationId) {

                'generateComponentSupportBundle' {
                    # Deliberately non-idempotent: always a new task.
                    $componentId = $routeValues['componentId']
                    $taskId = New-DeterministicGuid -Prefix 'a0000000'
                    $now = New-Timestamp
                    $task = [ordered]@{
                        id            = $taskId
                        name          = 'component_support_bundle_generation'
                        description   = [ordered]@{
                            id               = 'com.broadcom.lcm.ops.component.supportbundle.started'
                            defaultMessage   = "Started support bundle generation for component $componentId"
                            localizedMessage = "Started support bundle generation for component $componentId"
                            args             = [ordered]@{ componentId = $componentId }
                        }
                        status        = 'RUNNING'
                        type          = 'SUPPORT_BUNDLE_GENERATION'
                        createdBy     = 'admin'
                        resourceId    = $componentId
                        resourceType  = 'COMPONENT'
                        createTime    = $now
                        startTime     = $now
                        updateTime    = $now
                        retriable     = $false
                        cancellable   = $true
                    }
                    $corr = $req.Headers['X-Correlation-Id']
                    if ($corr) { $task['correlationId'] = $corr }
                    $script:Tasks[$taskId] = $task
                    $script:Polls[$taskId] = 0
                    $status = 202
                    $payload = $task
                }

                'getTasks' {
                    $q = [System.Web.HttpUtility]::ParseQueryString($rawQuery)
                    $items = @()
                    foreach ($t in $script:Tasks.Values) {
                        if ($q['resourceId']   -and $t['resourceId']   -ne $q['resourceId'])   { continue }
                        if ($q['resourceType'] -and $t['resourceType'] -ne $q['resourceType']) { continue }
                        if ($q['status']       -and $t['status']       -ne $q['status'])       { continue }
                        if ($q['type']         -and $t['type']         -ne $q['type'])         { continue }
                        $items += ConvertTo-TaskSummary $t
                    }
                    $payload = [ordered]@{
                        elements     = @($items)
                        pageMetadata = [ordered]@{
                            pageNumber    = 0
                            pageSize      = @($items).Count
                            totalElements = @($items).Count
                            totalPages    = 1
                        }
                    }
                }

                'getTask' {
                    $taskId = $routeValues['taskId']
                    if (-not $script:Tasks.Contains($taskId)) {
                        $status = 404
                        $payload = New-ErrorResponse -Code 'TASK_NOT_FOUND' -Message "No task $taskId."
                    }
                    else {
                        $task = $script:Tasks[$taskId]
                        if ($task['status'] -eq 'RUNNING') {
                            $script:Polls[$taskId]++
                            if ($script:Polls[$taskId] -ge $PollsBeforeSuccess) {
                                $task['status']  = 'SUCCEEDED'
                                $task['endTime'] = New-Timestamp
                                # Materialise the bundle this task produced.
                                $bundleId = New-DeterministicGuid -Prefix 'b0000000'
                                $script:Bundles[$bundleId] = [ordered]@{
                                    id               = $bundleId
                                    name             = "support-bundle-$($task['resourceId']).tgz"
                                    size             = 10485760
                                    createdTimestamp = $task['endTime']
                                    url              = "http://127.0.0.1:$Port/downloads/$bundleId.tgz"
                                    _componentId     = $task['resourceId']
                                }
                            }
                            $task['updateTime'] = New-Timestamp
                        }
                        $payload = $task
                    }
                }

                'getComponentSupportBundles' {
                    $componentId = $routeValues['componentId']
                    $items = @()
                    foreach ($b in $script:Bundles.Values) {
                        if ($b['_componentId'] -ne $componentId) { continue }
                        $copy = [ordered]@{}
                        foreach ($k in $b.Keys) { if ($k -ne '_componentId') { $copy[$k] = $b[$k] } }
                        $items += $copy
                    }
                    $payload = @($items)
                }
            }
        }

        # --------------------------------------------------------- request log
        $entry = [ordered]@{
            seq         = $script:Seq
            method      = $req.HttpMethod
            path        = $rawPath
            rawQuery    = $rawQuery
            matched     = [bool]$matchedRoute
            operationId = if ($matchedRoute) { $matchedRoute.OperationId } else { $null }
            status      = $status
            headers     = $headers
            body        = $body
            hasBody     = [bool]$req.HasEntityBody
        }
        Add-Content -LiteralPath $LogPath -Value ($entry | ConvertTo-Json -Depth 8 -Compress)

        # ------------------------------------------------------------ response
        $json = if ($payload -is [array]) {
            $payload | ConvertTo-Json -Depth 12 -AsArray
        } else {
            $payload | ConvertTo-Json -Depth 12
        }
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
        $res.StatusCode = $status
        $res.ContentType = 'application/json'
        $res.ContentLength64 = $bytes.Length
        $res.OutputStream.Write($bytes, 0, $bytes.Length)
        $res.OutputStream.Close()
    }
}
finally {
    $listener.Stop()
    $listener.Close()
}
