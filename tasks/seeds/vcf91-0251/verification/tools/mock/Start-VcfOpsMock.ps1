#Requires -Version 7.0
<#
.SYNOPSIS
    Loopback mock of the VCF Operations API, pinned to docs/contract.json.

.DESCRIPTION
    Serves ONLY the operations named in docs/contract.json. Every request is appended to a
    JSONL request log so tests can assert the exact wire shape that was sent.

    Anything the contract does not name is answered 404 and logged with operationId = null.
    The mock is deliberately strict: it rejects request bodies carrying properties the
    contract's schema does not declare, and rejects query parameters the contract does not
    declare. This is what "pinned to the contract" means here.

    Binds 127.0.0.1 on an ephemeral port and writes that port to <StateDir>/port so callers
    never have to guess. No external network access is performed.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $StateDir,
    [string] $ContractPath = (Join-Path $PSScriptRoot '../../docs/contract.json'),
    [string] $ScenarioPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$contract = Get-Content -LiteralPath $ContractPath -Raw | ConvertFrom-Json
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

$logPath  = Join-Path $StateDir 'requests.jsonl'
$portPath = Join-Path $StateDir 'port'
$pidPath  = Join-Path $StateDir 'pid'
Remove-Item -LiteralPath $logPath, $portPath -Force -ErrorAction SilentlyContinue

# ---------------------------------------------------------------- scenario ---
$scenario = [ordered]@{
    token          = 'mock-ops-token'
    reportId       = '7d0f0b3a-2b5e-4a1c-9c2f-1d6a8e4b3c55'
    statusSequence = @('QUEUED', 'RUNNING', 'COMPLETED')
    downloadBody   = "Resource,Metric,Value`nvcf-esx-01,cpu|demand,42`n"
    releaseName    = $contract.api.version
}
if ($ScenarioPath -and (Test-Path -LiteralPath $ScenarioPath)) {
    $override = Get-Content -LiteralPath $ScenarioPath -Raw | ConvertFrom-Json
    foreach ($p in $override.PSObject.Properties) { $scenario[$p.Name] = $p.Value }
}

# ----------------------------------------------------------------- routing ---
# Build the route table strictly from the contract: nothing else is served.
$routes = foreach ($prop in $contract.operations.PSObject.Properties) {
    $op = $prop.Value
    # /suite-api/api/reports/{id}/download  ->  ^/suite-api/api/reports/(?<id>[^/]+)/download$
    # Split the template on its placeholders first, then escape only the literal segments.
    $pattern = ([regex]::Split($op.requestPath, '(\{\w+\})') | ForEach-Object {
        if ($_ -match '^\{(\w+)\}$') { '(?<' + $Matches[1] + '>[^/]+)' } else { [regex]::Escape($_) }
    }) -join ''
    [pscustomobject]@{
        OperationId = $op.operationId
        Method      = $op.method
        Regex       = [regex]::new('^' + $pattern + '$')
        Op          = $op
    }
}

function New-Body {
    param([Parameter(Mandatory)] $Object, [int] $Depth = 10)
    if ($Object -is [string]) { return $Object }
    $Object | ConvertTo-Json -Depth $Depth -Compress
}

# ------------------------------------------------------------------ listen ---
$listener = $null
foreach ($attempt in 1..25) {
    $probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $probe.Start(); $candidate = $probe.LocalEndpoint.Port; $probe.Stop()
    try {
        $l = [System.Net.HttpListener]::new()
        $l.Prefixes.Add("http://127.0.0.1:$candidate/")
        $l.Start()
        $listener = $l
        $port = $candidate
        break
    } catch {
        if ($attempt -eq 25) { throw "Could not bind a loopback port after 25 attempts: $_" }
    }
}

$PID | Set-Content -LiteralPath $pidPath -NoNewline
# Written last: its presence is the readiness signal.
$port | Set-Content -LiteralPath $portPath -NoNewline
Write-Host "vcf-ops mock listening on http://127.0.0.1:$port/ (contract $($contract.contractVersion))"

$seq = 0
$getReportCalls = 0

try {
    while ($listener.IsListening) {
        $ctx = $listener.GetContext()
        $req = $ctx.Request
        $res = $ctx.Response
        $seq++

        $body = ''
        if ($req.HasEntityBody) {
            $reader = [System.IO.StreamReader]::new($req.InputStream, $req.ContentEncoding)
            $body = $reader.ReadToEnd(); $reader.Dispose()
        }

        $headers = [ordered]@{}
        foreach ($h in $req.Headers.AllKeys) { $headers[$h] = $req.Headers[$h] }

        $rawPath = $req.Url.AbsolutePath
        # VMware.Sdk.Vcf.Ops joins a trailing-slash service URI with a leading-slash base path and
        # emits '//suite-api/...'. Collapse repeated separators before routing; both forms are logged.
        $path  = [regex]::Replace($rawPath, '/{2,}', '/')
        $query = $req.Url.Query          # includes leading '?' when present, else ''

        $match = $null
        foreach ($r in $routes) {
            $m = $r.Regex.Match($path)
            if ($m.Success -and $r.Method -eq $req.HttpMethod) { $match = [pscustomobject]@{ Route = $r; Groups = $m }; break }
        }

        $status      = 200
        $contentType = 'application/json'
        $payload     = $null
        $reject      = $null

        if (-not $match) {
            $status  = 404
            $reject  = 'operation not in contract'
            $payload = New-Body @{ message = "No contract operation matches $($req.HttpMethod) $path" }
        } else {
            $op = $match.Route.Op

            # --- auth, exactly as the contract records it -------------------
            $authHeader = $req.Headers['Authorization']
            if ($op.authenticated -and $authHeader -ne ('OpsToken ' + $scenario.token)) {
                $status  = 401
                $reject  = 'missing or invalid Authorization header'
                $payload = New-Body @{ message = 'Authentication failed' }
            }

            # --- query parameters the contract does not declare -------------
            if (-not $reject) {
                $declared = @($op.queryParameters | ForEach-Object { $_.name })
                foreach ($key in $req.QueryString.AllKeys) {
                    if ($null -eq $key) { continue }
                    if ($declared -notcontains $key) {
                        $status  = 400
                        $reject  = "query parameter '$key' is not declared by operation $($op.operationId)"
                        $payload = New-Body @{ message = $reject }
                        break
                    }
                }
            }

            # --- request body against the contract's schema -----------------
            if (-not $reject -and $op.requestBody) {
                $parsed = $null
                try { $parsed = $body | ConvertFrom-Json -ErrorAction Stop } catch {
                    $status = 400; $reject = 'request body is not valid JSON'
                    $payload = New-Body @{ message = $reject }
                }
                if (-not $reject) {
                    $required = @($op.requestBody.required | ForEach-Object { $_.name })
                    $optional = @($op.requestBody.optional | ForEach-Object { $_.name })
                    $known    = $required + $optional
                    $present  = @($parsed.PSObject.Properties.Name)
                    foreach ($n in $present) {
                        if ($known -notcontains $n) {
                            $status = 400
                            $reject = "property '$n' is not declared by schema '$($op.requestBody.schema)'"
                            $payload = New-Body @{ message = $reject }
                            break
                        }
                    }
                    if (-not $reject) {
                        foreach ($n in $required) {
                            if ($present -notcontains $n) {
                                $status = 400
                                $reject = "required property '$n' is missing"
                                $payload = New-Body @{ message = $reject }
                                break
                            }
                        }
                    }
                }
            }

            # --- happy path -------------------------------------------------
            if (-not $reject) {
                switch ($op.operationId) {
                    'acquireToken' {
                        $payload = New-Body ([ordered]@{
                            token     = $scenario.token
                            validity  = 1893456000000
                            expiresAt = 'Tuesday, January 1, 2030 12:00:00 AM UTC'
                            roles     = @('Administrator')
                        })
                    }
                    'getCurrentVersionOfServer' {
                        $payload = New-Body ([ordered]@{
                            releaseName                = $scenario.releaseName
                            major                      = 9
                            minor                      = 1
                            minorMinor                 = 0
                            patch                      = 0
                            buildNumber                = 24000000
                            humanlyReadableReleaseDate = 'May 13, 2026'
                            releasedDate               = 1778976000000
                        })
                    }
                    'createReport' {
                        $incoming = $body | ConvertFrom-Json
                        $payload = New-Body ([ordered]@{
                            id                 = $scenario.reportId
                            reportDefinitionId = $incoming.reportDefinitionId
                            resourceId         = $incoming.resourceId
                            status             = @($scenario.statusSequence)[0]
                            owner              = 'admin'
                        })
                    }
                    'getReport' {
                        $seqArr = @($scenario.statusSequence)
                        $i = [Math]::Min($getReportCalls, $seqArr.Count - 1)
                        $getReportCalls++
                        $state = $seqArr[$i]
                        $r = [ordered]@{
                            id     = $match.Groups.Groups['id'].Value
                            status = $state
                        }
                        if ($state -eq 'COMPLETED') { $r['completionTime'] = '1778976123000' }
                        $payload = New-Body $r
                    }
                    'downloadReport' {
                        $fmt = $req.QueryString['format']
                        if ($fmt -eq 'PDF') {
                            $contentType = 'application/pdf'
                            $payload = "%PDF-1.4 mock report`n"
                        } else {
                            $contentType = 'text/csv'
                            $payload = $scenario.downloadBody
                        }
                    }
                }
            }
        }

        # ------------------------------------------------------------- log ---
        $entry = [ordered]@{
            seq          = $seq
            operationId  = if ($match) { $match.Route.OperationId } else { $null }
            method       = $req.HttpMethod
            path         = $path
            rawPath      = $rawPath
            query        = $query
            headers      = $headers
            body         = $body
            responseCode = $status
            rejected     = $reject
        }
        Add-Content -LiteralPath $logPath -Value ($entry | ConvertTo-Json -Depth 8 -Compress)

        $bytes = [System.Text.Encoding]::UTF8.GetBytes([string]$payload)
        $res.StatusCode      = $status
        $res.ContentType     = $contentType
        $res.ContentLength64 = $bytes.Length
        $res.OutputStream.Write($bytes, 0, $bytes.Length)
        $res.OutputStream.Close()
    }
} finally {
    if ($listener) { $listener.Stop(); $listener.Close() }
}
