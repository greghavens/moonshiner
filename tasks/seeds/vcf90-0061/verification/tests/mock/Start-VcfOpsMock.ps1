#Requires -Version 7.2
<#
.SYNOPSIS
    Loopback stand-in for a VCF Operations appliance, pinned to docs/contract.json.

.DESCRIPTION
    Listens on 127.0.0.1 only. The route table is built at start-up from the
    `operations` map in docs/contract.json, so the mock serves exactly the
    operationIds that the contract names and nothing else. Any other request is
    answered with 404 and recorded in the request log as a contract violation.

    Every request is appended to -LogPath as one JSON object per line before the
    response is written, so a test can read the log while the server is running.

    This process is a fake appliance, not a fake client: the module under test
    still has to speak real HTTP through VMware.Sdk.Vcf.Ops to reach it.

.NOTES
    Run as its own process. Stop it with Stop-Process; the log is flushed per
    request so nothing is lost.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [int]$Port,

    [Parameter(Mandatory)]
    [string]$LogPath,

    [Parameter(Mandatory)]
    [string]$ReadyPath,

    [string]$ContractPath = (Join-Path $PSScriptRoot '../../docs/contract.json'),

    [string]$FixturePath = (Join-Path $PSScriptRoot 'fixtures.json')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$contract = Get-Content -LiteralPath $ContractPath -Raw | ConvertFrom-Json
$fixtures = Get-Content -LiteralPath $FixturePath -Raw | ConvertFrom-Json

function ConvertTo-RouteRegex {
    param([string]$Template)

    $sb = [System.Text.StringBuilder]::new('^')
    $i = 0
    while ($i -lt $Template.Length) {
        $open = $Template.IndexOf('{', $i)
        if ($open -lt 0) {
            [void]$sb.Append([regex]::Escape($Template.Substring($i)))
            break
        }
        [void]$sb.Append([regex]::Escape($Template.Substring($i, $open - $i)))
        $close = $Template.IndexOf('}', $open)
        $name = $Template.Substring($open + 1, $close - $open - 1)
        [void]$sb.Append("(?<$name>[^/]+)")
        $i = $close + 1
    }
    [void]$sb.Append('$')
    $sb.ToString()
}

# --- Route table, derived from the contract --------------------------------
$routes = foreach ($name in $contract.operations.PSObject.Properties.Name) {
    $op = $contract.operations.$name
    [pscustomobject]@{
        OperationId   = $op.operationId
        Method        = $op.method.ToUpperInvariant()
        Regex         = [regex](ConvertTo-RouteRegex ($contract.basePath + $op.path))
        Authenticated = [bool]$op.authenticated
    }
}

# --- Simulated appliance state ---------------------------------------------
$issuedTokens = [System.Collections.Generic.HashSet[string]]::new()
$pollCounts = @{}
$taskToAction = @{}
foreach ($actionId in $fixtures.actions.PSObject.Properties.Name) {
    $taskToAction[$fixtures.actions.$actionId.taskId] = $actionId
}

$seq = 0

function Write-RequestLog {
    param(
        [hashtable]$Entry
    )
    $line = ($Entry | ConvertTo-Json -Depth 8 -Compress)
    [System.IO.File]::AppendAllText($LogPath, $line + [Environment]::NewLine)
}

function Get-HeaderMap {
    param($Request)
    $map = @{}
    foreach ($key in $Request.Headers.AllKeys) {
        $map[$key.ToLowerInvariant()] = $Request.Headers[$key]
    }
    $map
}

$listener = $null

# Choose the ephemeral port in the mock process, immediately before binding
# the real listener. If another process wins the tiny release/rebind race,
# choose another port instead of making verification flaky.
if ($Port -eq 0) {
    foreach ($attempt in 1..20) {
        $probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
        $probe.Start()
        $Port = ([System.Net.IPEndPoint]$probe.LocalEndpoint).Port
        $probe.Stop()

        $candidate = [System.Net.HttpListener]::new()
        $candidate.Prefixes.Add("http://127.0.0.1:$Port/")
        try {
            $candidate.Start()
            $listener = $candidate
            break
        }
        catch {
            $candidate.Close()
            if ($attempt -eq 20) { throw }
        }
    }
}
else {
    $listener = [System.Net.HttpListener]::new()
    $listener.Prefixes.Add("http://127.0.0.1:$Port/")
    $listener.Start()
}

if (Test-Path -LiteralPath $LogPath) { Remove-Item -LiteralPath $LogPath -Force }
[System.IO.File]::WriteAllText($LogPath, '')
[System.IO.File]::WriteAllText($ReadyPath, "$Port")

try {
    while ($listener.IsListening) {
        $ctx = $listener.GetContext()
        $req = $ctx.Request
        $seq++

        $body = $null
        if ($req.HasEntityBody) {
            $reader = [System.IO.StreamReader]::new($req.InputStream, $req.ContentEncoding)
            $body = $reader.ReadToEnd()
            $reader.Dispose()
        }

        $rawPath = $req.Url.AbsolutePath
        # The client emits '//suite-api/...' on some code paths. Collapse runs of
        # slashes for routing; the untouched request line is kept in rawUrl.
        $absPath = $rawPath -replace '/{2,}', '/'
        $query = $req.Url.Query          # '' or '?detail=true'
        $method = $req.HttpMethod.ToUpperInvariant()

        $matchedRoute = $null
        $routeMatch = $null
        foreach ($route in $routes) {
            if ($route.Method -ne $method) { continue }
            $m = $route.Regex.Match($absPath)
            if ($m.Success) {
                $matchedRoute = $route
                $routeMatch = $m
                break
            }
        }

        $status = 200
        $payload = $null

        if ($null -eq $matchedRoute) {
            $status = 404
            $payload = @{
                message = "No operation in docs/contract.json serves $method $absPath."
            }
        }
        else {
            $headers = Get-HeaderMap $req
            $authorization = if ($headers.ContainsKey('authorization')) { $headers['authorization'] } else { $null }

            if ($matchedRoute.Authenticated) {
                $prefix = $contract.auth.valuePrefix
                $tokenOk = $authorization -and
                    $authorization.StartsWith($prefix, [System.StringComparison]::Ordinal) -and
                    $issuedTokens.Contains($authorization.Substring($prefix.Length))
                if (-not $tokenOk) {
                    $status = 401
                    $payload = @{ message = 'Missing or unknown session token.' }
                }
            }

            if ($status -eq 200) {
                switch ($matchedRoute.OperationId) {
                    'acquireToken' {
                        [void]$issuedTokens.Add($fixtures.authToken.token)
                        $payload = @{
                            token     = $fixtures.authToken.token
                            validity  = $fixtures.authToken.validity
                            expiresAt = $fixtures.authToken.expiresAt
                            roles     = @($fixtures.authToken.roles)
                        }
                    }

                    'getCurrentVersionOfServer' {
                        $payload = @{
                            releaseName                = $fixtures.version.releaseName
                            major                      = $fixtures.version.major
                            minor                      = $fixtures.version.minor
                            minorMinor                 = $fixtures.version.minorMinor
                            releasedDate               = $fixtures.version.releasedDate
                            humanlyReadableReleaseDate = $fixtures.version.humanlyReadableReleaseDate
                        }
                    }

                    'performAction' {
                        $actionId = [uri]::UnescapeDataString($routeMatch.Groups['id'].Value)
                        $known = $fixtures.actions.PSObject.Properties.Name
                        if ($actionId -notin $known) {
                            $status = 404
                            $payload = @{ message = "Unknown action id '$actionId'." }
                        }
                        else {
                            $taskId = $fixtures.actions.$actionId.taskId
                            $pollCounts[$taskId] = 0
                            $payload = @{ values = @($taskId) }
                        }
                    }

                    'getActionStatus' {
                        $taskId = [uri]::UnescapeDataString($routeMatch.Groups['taskId'].Value)
                        if (-not $taskToAction.ContainsKey($taskId)) {
                            $status = 404
                            $payload = @{ message = "Unknown task id '$taskId'." }
                        }
                        else {
                            $action = $fixtures.actions.($taskToAction[$taskId])
                            $index = if ($pollCounts.ContainsKey($taskId)) { $pollCounts[$taskId] } else { 0 }
                            $states = @($action.stateSequence)
                            $state = $states[[Math]::Min($index, $states.Count - 1)]
                            $pollCounts[$taskId] = $index + 1

                            $isLast = $index -ge ($states.Count - 1)

                            $payload = @{
                                taskId       = $taskId
                                name         = $action.name
                                state        = $state
                                resourceKind = $action.resourceKind
                                submittedBy  = 'admin'
                                authSource   = 'LOCAL'
                                startDate    = '2025-06-17T09:57:20.439Z'
                            }
                            if ($isLast) {
                                $payload['completeDate'] = '2025-06-17T09:57:24.512Z'
                                $payload['messages'] = @(
                                    foreach ($msg in $action.messages) {
                                        @{
                                            message   = $msg.message
                                            level     = $msg.level
                                            timestamp = '2025-06-17T09:57:24.512Z'
                                        }
                                    }
                                )
                                if ($null -ne $action.actionResult) {
                                    $payload['actionResult'] = $action.actionResult
                                }
                            }

                            $detailRaw = $req.QueryString['detail']
                            $detail = $detailRaw -and [string]::Equals($detailRaw, 'true', [System.StringComparison]::OrdinalIgnoreCase)
                            if ($detail) {
                                $payload['actionObjectStatuses'] = @(
                                    @{
                                        id           = $taskId
                                        state        = $state
                                        creationDate = '2025-06-17T09:57:20.439Z'
                                    }
                                )
                            }
                        }
                    }

                    default {
                        $status = 501
                        $payload = @{ message = "Route '$($matchedRoute.OperationId)' has no handler." }
                    }
                }
            }
        }

        Write-RequestLog @{
            seq         = $seq
            observedAtMilliseconds = [Environment]::TickCount64
            method      = $method
            path        = $absPath
            rawPath     = $rawPath
            query       = $query
            rawUrl      = $req.RawUrl
            headers     = (Get-HeaderMap $req)
            body        = $body
            operationId = if ($matchedRoute) { $matchedRoute.OperationId } else { $null }
            servedByContract = [bool]$matchedRoute
            statusCode  = $status
        }

        $bytes = [System.Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 8 -Compress))
        $ctx.Response.StatusCode = $status
        $ctx.Response.ContentType = 'application/json'
        $ctx.Response.ContentLength64 = $bytes.Length
        $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
        $ctx.Response.OutputStream.Close()
        $ctx.Response.Close()
    }
}
finally {
    if ($listener.IsListening) { $listener.Stop() }
    $listener.Close()
}
