<#
.SYNOPSIS
    Loopback mock of the VCF Operations suite-api, pinned to docs/contract.json.

.DESCRIPTION
    Serves only the operations named in docs/contract.json. Any other method/path
    pair is answered 404 and logged with contractViolation = true, so a client that
    strays outside the contract is caught rather than silently tolerated.

    Every request is appended to -LogPath as one JSON object per line. The log is
    the only channel the verifier reads; the mock exposes no control endpoints.

    Token lifetime is driven by the fixture: the Nth acquired token is revoked once
    it has been used for tokenBudgets[N-1] authorized requests. That is how the
    "token expires part way through the run" scenario is produced -- deterministically,
    by request count, never by wall-clock time.

    Binds to 127.0.0.1 only. It contacts nothing.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [int]    $Port,
    [Parameter(Mandatory)] [string] $FixturePath,
    [Parameter(Mandatory)] [string] $LogPath,
    [Parameter(Mandatory)] [string] $ContractPath,
    [string] $ReadyPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$contract = Get-Content -LiteralPath $ContractPath -Raw | ConvertFrom-Json
$fixture  = Get-Content -LiteralPath $FixturePath  -Raw | ConvertFrom-Json
$basePath = $contract.basePath

# ---------------------------------------------------------------- route table
# Built from the contract so the mock cannot drift from it: a route exists here
# only because an operation in docs/contract.json declares it.
$routes = @()
foreach ($name in $contract.operations.PSObject.Properties.Name) {
    $op = $contract.operations.$name
    # /api/alerts/{id}/notes -> regex with a named capture for the path parameter.
    $pattern = [regex]::Escape($basePath + $op.pathTemplate)
    $pattern = $pattern -replace '\\\{(\w+)\\\}', '(?<$1>[^/]+)'
    $routes += [pscustomobject]@{
        OperationId = $op.operationId
        Method      = $op.method
        Pattern     = '^' + $pattern + '$'
        RequiresAuth = $op.requiresAuthorization
    }
}

# ------------------------------------------------------------------- state
$state = [ordered]@{
    Seq          = 0
    AcquireCount = 0
    ActiveToken  = $null
    TokenUses    = 0
    TokenBudget  = 0
}
$budgets = @($fixture.tokenBudgets)
$alerts  = @($fixture.alerts)
if ($fixture.PSObject.Properties.Name -contains 'inheritAlertsFrom') {
    $parent = Join-Path (Split-Path -Parent $FixturePath) $fixture.inheritAlertsFrom
    $alerts = @((Get-Content -LiteralPath $parent -Raw | ConvertFrom-Json).alerts)
}

function Write-Log([hashtable]$Entry) {
    $line = ([pscustomobject]$Entry | ConvertTo-Json -Compress -Depth 12)
    [System.IO.File]::AppendAllText($LogPath, $line + "`n", [System.Text.UTF8Encoding]::new($false))
}

function Get-BodyKeys([string]$Raw) {
    # Surface the literal top-level keys the client put on the wire. A field that
    # was serialized as null still shows up here -- that is the point: the verifier
    # must be able to tell "omitted" from "present and empty".
    if ([string]::IsNullOrWhiteSpace($Raw)) { return @() }
    try {
        $obj = $Raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        return @('<unparseable>')
    }
    if ($null -eq $obj -or $obj -isnot [psobject]) { return @() }
    return @($obj.PSObject.Properties.Name | Sort-Object)
}

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://127.0.0.1:$Port/")
$listener.Start()

if ($ReadyPath) {
    [System.IO.File]::WriteAllText($ReadyPath, "$Port")
}

try {
    while ($listener.IsListening) {
        $ctx = $listener.GetContext()
        $req = $ctx.Request
        $res = $ctx.Response

        # The SDK emits "//suite-api/..." (double slash). Normalize before routing.
        $path = ($req.Url.AbsolutePath -replace '/{2,}', '/')
        $method = $req.HttpMethod

        $raw = ''
        if ($req.HasEntityBody) {
            $reader = [System.IO.StreamReader]::new($req.InputStream, [System.Text.Encoding]::UTF8)
            $raw = $reader.ReadToEnd()
            $reader.Dispose()
        }

        $query = [ordered]@{}
        foreach ($key in $req.Url.Query.TrimStart('?').Split('&')) {
            if ($key) {
                $kv = $key.Split('=', 2)
                $query[$kv[0]] = if ($kv.Count -gt 1) { $kv[1] } else { '' }
            }
        }

        $auth = $req.Headers['Authorization']
        $state.Seq++

        $match = $null
        $route = $null
        foreach ($r in $routes) {
            if ($r.Method -ne $method) { continue }
            $m = [regex]::Match($path, $r.Pattern)
            if ($m.Success) { $route = $r; $match = $m; break }
        }

        $status = 200
        $payload = $null
        $note = $null

        if (-not $route) {
            $status = 404
            $payload = @{ message = "no operation in the contract matches $method $path" }
            $note = 'off-contract'
        }
        elseif ($route.OperationId -eq 'acquireToken') {
            $state.AcquireCount++
            $state.ActiveToken = 'ops-token-{0}' -f $state.AcquireCount
            $state.TokenUses = 0
            $idx = $state.AcquireCount - 1
            $state.TokenBudget = if ($idx -lt $budgets.Count) { [int]$budgets[$idx] } else { 0 }
            $payload = [ordered]@{
                token     = $state.ActiveToken
                validity  = [int64]$fixture.tokenValidity
                expiresAt = [string]$fixture.tokenExpiresAt
                roles     = @($fixture.roles)
            }
        }
        elseif ($route.RequiresAuth -and $auth -ne ('OpsToken ' + $state.ActiveToken)) {
            $status = 401
            $payload = @{ message = 'the supplied token is not valid' }
            $note = 'rejected-stale-token'
        }
        else {
            $state.TokenUses++
            if ($state.TokenBudget -gt 0 -and $state.TokenUses -gt $state.TokenBudget) {
                # Budget exhausted: the server revokes the token mid-run. The client
                # gets 401 on a request it had every reason to believe was authorized.
                $state.ActiveToken = $null
                $status = 401
                $payload = @{ message = 'the supplied token has expired' }
                $note = 'token-expired'
            }
            else {
                switch ($route.OperationId) {
                    'getCurrentVersionOfServer' {
                        $payload = [ordered]@{
                            major = 9; minor = 1; patch = 0; minorMinor = 0
                            buildNumber = [int]$fixture.buildNumber
                            releaseName = [string]$fixture.releaseName
                            description = 'VMware Cloud Foundation Operations'
                            humanlyReadableReleaseDate = [string]$fixture.releaseDate
                        }
                    }
                    'releaseToken' {
                        $state.ActiveToken = $null
                        $payload = ''
                    }
                    'queryAlert' {
                        $page = if ($query.Contains('page')) { [int]$query['page'] } else { 0 }
                        $size = if ($query.Contains('pageSize')) { [int]$query['pageSize'] } else { 1000 }
                        $selected = @($alerts)
                        # Honour the two filters the contract lets this client set, so a
                        # client that sends the wrong filter gets the wrong alert set.
                        $body = if ($raw) { $raw | ConvertFrom-Json } else { $null }
                        if ($body -and $body.PSObject.Properties.Name -contains 'activeOnly' -and $body.activeOnly) {
                            $selected = @($selected | Where-Object { $_.status -eq 'ACTIVE' })
                        }
                        if ($body -and $body.PSObject.Properties.Name -contains 'alertCriticality' -and $body.alertCriticality) {
                            $want = @($body.alertCriticality)
                            $selected = @($selected | Where-Object { $want -contains $_.alertLevel })
                        }
                        if ($body -and $body.PSObject.Properties.Name -contains 'resourceKind' -and $body.resourceKind) {
                            $selected = @($selected | Where-Object { $_.resourceKind -eq $body.resourceKind })
                        }
                        $total = $selected.Count
                        $slice = @()
                        if ($size -gt 0 -and ($page * $size) -lt $total) {
                            $slice = @($selected[($page * $size) .. ([Math]::Min($page * $size + $size, $total) - 1)])
                        }
                        $payload = [ordered]@{
                            alerts   = @($slice | ForEach-Object {
                                [ordered]@{
                                    alertId             = $_.alertId
                                    resourceId          = $_.resourceId
                                    alertLevel          = $_.alertLevel
                                    status              = $_.status
                                    controlState        = $_.controlState
                                    alertImpact         = $_.alertImpact
                                    type                = $_.type
                                    subType             = $_.subType
                                    startTimeUTC        = [int64]$_.startTimeUTC
                                    updateTimeUTC       = [int64]$_.updateTimeUTC
                                    alertDefinitionId   = $_.alertDefinitionId
                                    alertDefinitionName = $_.alertDefinitionName
                                }
                            })
                            pageInfo = [ordered]@{
                                page = $page; pageSize = $size; totalCount = $total
                            }
                        }
                    }
                    'modifyAlerts' {
                        $body = if ($raw) { $raw | ConvertFrom-Json } else { $null }
                        $ids = @()
                        if ($body -and $body.PSObject.Properties.Name -contains 'uuids') {
                            $ids = @($body.uuids)
                        }
                        if (-not $query.Contains('action')) {
                            $status = 400
                            $payload = @{ message = 'the action query parameter is required' }
                            $note = 'missing-action'
                        }
                        elseif ($ids.Count -eq 0) {
                            $status = 400
                            $payload = @{ message = 'uuids must name at least one alert' }
                            $note = 'empty-uuids'
                        }
                        else {
                            $unknown = @($ids | Where-Object { $id = $_; -not ($alerts | Where-Object { $_.alertId -eq $id }) })
                            if ($unknown.Count -gt 0) {
                                $status = 404
                                $payload = @{ message = "no alert with id $($unknown[0])" }
                                $note = 'unknown-alert'
                            }
                            else {
                                $touched = @($alerts | Where-Object { $ids -contains $_.alertId })
                                $payload = [ordered]@{
                                    alerts = @($touched | ForEach-Object {
                                        [ordered]@{
                                            alertId       = $_.alertId
                                            resourceId    = $_.resourceId
                                            alertLevel    = $_.alertLevel
                                            status        = $_.status
                                            controlState  = 'SUSPENDED'
                                            alertImpact   = $_.alertImpact
                                            type          = $_.type
                                            subType       = $_.subType
                                            startTimeUTC  = [int64]$_.startTimeUTC
                                            updateTimeUTC = [int64]$_.updateTimeUTC
                                        }
                                    })
                                    pageInfo = [ordered]@{ page = 0; pageSize = $ids.Count; totalCount = $touched.Count }
                                }
                            }
                        }
                    }
                }
            }
        }

        Write-Log @{
            seq              = $state.Seq
            operationId      = if ($route) { $route.OperationId } else { $null }
            method           = $method
            path             = $path
            rawPath          = $req.Url.AbsolutePath
            query            = $query
            authorization    = $auth
            contentType      = $req.ContentType
            body             = $raw
            bodyKeys         = @(Get-BodyKeys $raw)
            status           = $status
            note             = $note
            contractViolation = (-not $route)
            tokenOrdinal     = $state.AcquireCount
        }

        $bytes = if ($null -eq $payload -or $payload -is [string]) {
            [System.Text.Encoding]::UTF8.GetBytes([string]$payload)
        } else {
            [System.Text.Encoding]::UTF8.GetBytes(([pscustomobject]$payload | ConvertTo-Json -Compress -Depth 12))
        }
        $res.StatusCode = $status
        $res.ContentType = 'application/json'
        $res.ContentLength64 = $bytes.Length
        $res.OutputStream.Write($bytes, 0, $bytes.Length)
        $res.Close()
    }
}
finally {
    if ($listener.IsListening) { $listener.Stop() }
    $listener.Close()
}
