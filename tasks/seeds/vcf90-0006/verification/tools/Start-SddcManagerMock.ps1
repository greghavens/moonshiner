<#
.SYNOPSIS
    Loopback mock of the VMware Cloud Foundation 9.0 SDDC Manager REST API.

.DESCRIPTION
    Serves only the four operations named in docs/contract.json:

        createToken                      POST /v1/tokens                    -> 201 TokenPair
        validateHostCommissionSpec       POST /v1/hosts/validations         -> 202 Validation
        getHostCommissionValidationByID  GET  /v1/hosts/validations/{id}    -> 202 Validation
        commissionHosts                  POST /v1/hosts                     -> 202 Task

    Every other request is answered 404 with an Error body. Every request that
    reaches the listener -- in contract or not -- is appended to the request log
    as one compact JSON object per line, flushed before the response is written.

.PARAMETER Port
    TCP port to bind on 127.0.0.1.

.PARAMETER RequestLogPath
    Path of the JSON Lines request log. Truncated on start.

.PARAMETER Scenario
    PrecheckPasses             validation completes SUCCEEDED immediately
    PrecheckFails              validation completes FAILED immediately
    PrecheckPendingThenPasses  validation starts IN_PROGRESS; the first poll is
                               still IN_PROGRESS, later polls are SUCCEEDED
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][int]$Port,
    [Parameter(Mandatory)][string]$RequestLogPath,
    [Parameter(Mandatory)][ValidateSet('PrecheckPasses', 'PrecheckFails', 'PrecheckPendingThenPasses')]
    [string]$Scenario
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$contractPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'docs/contract.json'
if (-not (Test-Path -LiteralPath $contractPath)) {
    throw "contract not found at $contractPath"
}
$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json

# The mock is pinned to the contract: it refuses to serve anything the contract
# does not name, and it answers with the status code the contract records.
$routes = @{}
foreach ($op in $contract.operations) {
    $routes[$op.operationId] = [pscustomobject]@{
        Method  = $op.method
        Path    = $op.path
        Status  = [int]$op.successStatus
        Pattern = '^' + ([regex]::Escape($op.path) -replace '\\?\{[A-Za-z0-9_]+\\?\}', '([^/]+)') + '$'
    }
}
foreach ($required in @('createToken', 'validateHostCommissionSpec', 'getHostCommissionValidationByID', 'commissionHosts')) {
    if (-not $routes.ContainsKey($required)) { throw "contract does not name operation '$required'" }
}

$validationId = 'validation-0001'
$accessToken = 'mock-access-token'
$pollCount = 0

function New-Validation {
    param([string]$ExecutionStatus, [string]$ResultStatus, [switch]$WithFailedCheck)
    $v = [ordered]@{
        id              = $validationId
        description     = 'Host commission validation'
        executionStatus = $ExecutionStatus
        resultStatus    = $ResultStatus
    }
    if ($WithFailedCheck) {
        $v['validationChecks'] = @(
            [ordered]@{
                description  = 'Host is not already part of an existing domain'
                severity     = 'ERROR'
                resultStatus = 'FAILED'
                errorResponse = [ordered]@{
                    errorCode = 'HOST_ALREADY_COMMISSIONED'
                    message   = 'The host is already commissioned in this SDDC Manager instance.'
                }
            }
        )
    }
    return $v
}

function Resolve-ValidationBody {
    param([switch]$IsPoll)
    switch ($Scenario) {
        'PrecheckPasses' { return (New-Validation -ExecutionStatus 'COMPLETED' -ResultStatus 'SUCCEEDED') }
        'PrecheckFails' { return (New-Validation -ExecutionStatus 'COMPLETED' -ResultStatus 'FAILED' -WithFailedCheck) }
        'PrecheckPendingThenPasses' {
            if (-not $IsPoll) { return (New-Validation -ExecutionStatus 'IN_PROGRESS' -ResultStatus 'UNKNOWN') }
            $script:pollCount++
            if ($script:pollCount -le 1) { return (New-Validation -ExecutionStatus 'IN_PROGRESS' -ResultStatus 'UNKNOWN') }
            return (New-Validation -ExecutionStatus 'COMPLETED' -ResultStatus 'SUCCEEDED')
        }
    }
}

Set-Content -LiteralPath $RequestLogPath -Value '' -NoNewline -Encoding utf8

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://127.0.0.1:$Port/")
$listener.Start()

try {
    while ($listener.IsListening) {
        $context = $listener.GetContext()
        $request = $context.Request

        $body = ''
        if ($request.HasEntityBody) {
            $reader = [System.IO.StreamReader]::new($request.InputStream, $request.ContentEncoding)
            $body = $reader.ReadToEnd()
            $reader.Close()
        }

        $headers = [ordered]@{}
        foreach ($name in $request.Headers.AllKeys) { $headers[$name] = $request.Headers[$name] }

        $path = $request.Url.AbsolutePath
        $entry = [ordered]@{
            method  = $request.HttpMethod.ToUpperInvariant()
            path    = $path
            query   = if ($request.RawUrl.Contains('?')) { $request.RawUrl.Substring($request.RawUrl.IndexOf('?') + 1) } else { '' }
            body    = $body
            headers = $headers
        }
        $line = ($entry | ConvertTo-Json -Depth 8 -Compress)
        $writer = [System.IO.StreamWriter]::new($RequestLogPath, $true, [System.Text.UTF8Encoding]::new($false))
        $writer.WriteLine($line)
        $writer.Flush()
        $writer.Close()

        $status = 404
        $payload = [ordered]@{
            errorCode = 'NOT_FOUND'
            errorType = 'NOT_SUPPORTED'
            message   = "No contract operation serves $($request.HttpMethod) $path."
        }

        $matched = $null
        foreach ($operationId in $routes.Keys) {
            $route = $routes[$operationId]
            if ($request.HttpMethod.ToUpperInvariant() -eq $route.Method -and $path -match $route.Pattern) {
                $matched = $operationId
                break
            }
        }

        switch ($matched) {
            'createToken' {
                $status = $routes['createToken'].Status
                $payload = [ordered]@{
                    accessToken  = $accessToken
                    refreshToken = [ordered]@{ id = 'mock-refresh-token' }
                }
            }
            'validateHostCommissionSpec' {
                $status = $routes['validateHostCommissionSpec'].Status
                $payload = Resolve-ValidationBody
            }
            'getHostCommissionValidationByID' {
                $status = $routes['getHostCommissionValidationByID'].Status
                $payload = Resolve-ValidationBody -IsPoll
            }
            'commissionHosts' {
                $status = $routes['commissionHosts'].Status
                $payload = [ordered]@{
                    id                = 'task-0001'
                    name              = 'Commissioning Hosts'
                    type              = 'HOST_COMMISSION'
                    status            = 'IN_PROGRESS'
                    creationTimestamp = '2026-01-01T00:00:00.000Z'
                }
            }
        }

        $json = $payload | ConvertTo-Json -Depth 8 -Compress
        $buffer = [System.Text.Encoding]::UTF8.GetBytes($json)
        $context.Response.StatusCode = $status
        $context.Response.ContentType = 'application/json'
        $context.Response.ContentLength64 = $buffer.Length
        $context.Response.OutputStream.Write($buffer, 0, $buffer.Length)
        $context.Response.Close()
    }
}
finally {
    if ($listener.IsListening) { $listener.Stop() }
    $listener.Close()
}
