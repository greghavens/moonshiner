<#
.SYNOPSIS
    Loopback contract mock for the VCF Operations API operations named in docs/contract.json.

.DESCRIPTION
    Serves ONLY the operations listed in the pinned contract projection. Any other
    method/path combination is answered 404 and recorded in the request log so the
    verifier can prove no off-contract route was called.

    Every request is appended to -LogPath as one JSON object per line, flushed
    immediately, so a separate process can read the log while the mock is running.

    This process never reaches the network beyond the loopback interface it binds.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $ContractPath,
    [Parameter(Mandatory)][int]    $Port,
    [Parameter(Mandatory)][string] $LogPath,
    [Parameter(Mandatory)][string] $ReadyPath,
    [ValidateSet('precheck-pass', 'precheck-fail')]
    [string] $Scenario = 'precheck-pass'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$contract = Get-Content -LiteralPath $ContractPath -Raw | ConvertFrom-Json
$basePath = $contract.basePath

# ---------------------------------------------------------------------------
# Route table: built from the contract, nothing else is reachable.
# ---------------------------------------------------------------------------
$routes = [ordered]@{}
foreach ($op in $contract.operations) {
    $routes[('{0} {1}{2}' -f $op.method, $basePath, $op.path)] = $op.operationId
}

$AUTH_TOKEN     = 'a4d63c0e-2f18-4d0a-9b56-70c1c1e4a2f7'
$CREATED_ID     = '725cbdae-812e-4e98-9972-53c58f51661b'
$PRECHECK_ID    = 'c1f0a2be-6d84-4f1b-8f4c-2b7a0f5d9e33'
$PRECHECK_ERROR = 'Cannot establish a connection to the data source. The certificate presented by the endpoint is not trusted.'

function New-AdapterInstanceBody {
    param([string] $Id, [string] $Name, [string] $AdapterKindKey)
    [ordered]@{
        resourceKey = [ordered]@{
            name                = $Name
            adapterKindKey      = $AdapterKindKey
            resourceKindKey     = 'VMwareAdapter Instance'
            resourceIdentifiers = @()
        }
        collectorId          = 1
        credentialInstanceId = '6f455a29-3330-47b6-9128-a608bca9d2c7'
        id                   = $Id
    }
}

function Get-RequestNames {
    param([string] $Body)
    $name = ''
    $kind = ''
    if (-not [string]::IsNullOrWhiteSpace($Body)) {
        try {
            $parsed = $Body | ConvertFrom-Json
            if ($parsed.PSObject.Properties.Name -contains 'name')           { $name = [string]$parsed.name }
            if ($parsed.PSObject.Properties.Name -contains 'adapterKindKey') { $kind = [string]$parsed.adapterKindKey }
        } catch {
            # A malformed body is still logged verbatim; the verifier judges it.
        }
    }
    , @($name, $kind)
}

# ---------------------------------------------------------------------------
# Request log
# ---------------------------------------------------------------------------
$logDir = Split-Path -Parent $LogPath
if ($logDir -and -not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
Set-Content -LiteralPath $LogPath -Value '' -NoNewline -Encoding utf8

$sequence = 0
function Write-RequestLog {
    param([hashtable] $Record)
    $line = ($Record | ConvertTo-Json -Depth 8 -Compress)
    for ($attempt = 0; $attempt -lt 50; $attempt++) {
        try {
            $stream = [System.IO.File]::Open(
                $LogPath, [System.IO.FileMode]::Append,
                [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
            try {
                $bytes = [System.Text.Encoding]::UTF8.GetBytes($line + "`n")
                $stream.Write($bytes, 0, $bytes.Length)
                $stream.Flush()
            } finally {
                $stream.Dispose()
            }
            return
        } catch [System.IO.IOException] {
            Start-Sleep -Milliseconds 20
        }
    }
    throw "contract mock: unable to append to request log '$LogPath'"
}

# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add(('http://127.0.0.1:{0}/' -f $Port))
$listener.Start()
Set-Content -LiteralPath $ReadyPath -Value $Port -Encoding utf8

try {
    while ($listener.IsListening) {
        $context = $listener.GetContext()
        $request = $context.Request
        $response = $context.Response

        $buffer = [System.IO.MemoryStream]::new()
        $request.InputStream.CopyTo($buffer)
        $bodyBytes = $buffer.ToArray()
        $buffer.Dispose()
        $body = [System.Text.Encoding]::UTF8.GetString($bodyBytes)

        $headers = [ordered]@{}
        foreach ($key in $request.Headers.AllKeys) {
            $headers[$key.ToLowerInvariant()] = @($request.Headers.GetValues($key))
        }

        # Routing collapses repeated separators, as an origin server does. The
        # verbatim target is still logged, so nothing about the wire is hidden.
        $normalizedPath = [regex]::Replace($request.Url.AbsolutePath, '/{2,}', '/')
        $routeKey = '{0} {1}' -f $request.HttpMethod, $normalizedPath
        $operationId = if ($routes.Contains($routeKey)) { $routes[$routeKey] } else { $null }

        $sequence++
        Write-RequestLog @{
            sequence         = $sequence
            operationId      = $operationId
            method           = $request.HttpMethod
            rawTarget        = $request.RawUrl
            path             = $request.Url.AbsolutePath
            normalizedPath   = $normalizedPath
            normalizedTarget = $normalizedPath + $request.Url.Query
            query            = $request.Url.Query
            headers          = $headers
            bodyByteCount    = $bodyBytes.Length
            body             = $body
        }

        $status = 404
        $payload = $null

        switch ($operationId) {
            'acquireToken' {
                $status = 200
                $payload = [ordered]@{
                    token     = $AUTH_TOKEN
                    validity  = 1893456000000
                    expiresAt = 'Tuesday, January 1, 2030 12:00:00 AM UTC'
                    roles     = @('ADMIN')
                }
            }
            'getCurrentVersionOfServer' {
                $status = 200
                $payload = [ordered]@{
                    releaseName                = 'VCF Operations 9.1.0.0'
                    major                      = 9
                    minor                      = 1
                    minorMinor                 = 0
                    patch                      = 0
                    buildNumber                = 24000000
                    releasedDate               = 1772568000000
                    humanlyReadableReleaseDate = 'Tuesday, March 3, 2026 at 12:00:00 PM UTC'
                }
            }
            'testConnection' {
                $names = Get-RequestNames -Body $body
                if ($Scenario -eq 'precheck-fail') {
                    $status = 400
                    $payload = [ordered]@{
                        message        = $PRECHECK_ERROR
                        httpStatusCode = 400
                        apiErrorCode   = 1400
                    }
                } else {
                    $status = 201
                    $payload = New-AdapterInstanceBody -Id $PRECHECK_ID -Name $names[0] -AdapterKindKey $names[1]
                }
            }
            'createAdapterInstance' {
                $names = Get-RequestNames -Body $body
                $status = 201
                $payload = New-AdapterInstanceBody -Id $CREATED_ID -Name $names[0] -AdapterKindKey $names[1]
            }
            'releaseToken' {
                $status = 200
                $payload = $null
            }
            default {
                $status = 404
                $payload = [ordered]@{
                    message        = 'Route is not part of the pinned contract projection.'
                    httpStatusCode = 404
                }
            }
        }

        $response.StatusCode = $status
        if ($null -eq $payload) {
            $response.ContentLength64 = 0
        } else {
            $json = $payload | ConvertTo-Json -Depth 8 -Compress
            $out = [System.Text.Encoding]::UTF8.GetBytes($json)
            $response.ContentType = 'application/json'
            $response.ContentLength64 = $out.Length
            $response.OutputStream.Write($out, 0, $out.Length)
        }
        $response.OutputStream.Close()
    }
} finally {
    if ($listener.IsListening) { $listener.Stop() }
    $listener.Close()
}
