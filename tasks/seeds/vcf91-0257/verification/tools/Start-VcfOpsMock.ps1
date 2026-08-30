<#
.SYNOPSIS
    Loopback mock of the VCF Operations API, pinned to docs/contract.json.

.DESCRIPTION
    Serves ONLY the operations named in docs/contract.json. Any other
    method/path pair is answered 404 and flagged in the request log as
    out-of-contract.

    Every request is appended to -LogPath as one JSON object per line so a
    test can assert the exact wire shape after the fact.

    This is a stand-in for the appliance, not for the PowerCLI client: the
    VMware.Sdk.Vcf.Ops cmdlets under test perform real HTTP against it.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [int]    $Port,
    [Parameter(Mandatory)] [string] $LogPath,
    [Parameter(Mandatory)] [string] $ContractPath,
    [Parameter(Mandatory)] [string] $StatePath,
    [string] $ReadyPath
)

$ErrorActionPreference = 'Stop'

$contract = Get-Content -Raw -LiteralPath $ContractPath | ConvertFrom-Json
$state    = Get-Content -Raw -LiteralPath $StatePath    | ConvertFrom-Json
$basePath = $contract.basePath

# (METHOD, path) -> operationId, straight out of the contract.
$routes = @{}
foreach ($name in $contract.operations.PSObject.Properties.Name) {
    $op = $contract.operations.$name
    $routes["$($op.method) $($op.path)"] = $name
}

$script:seq        = 0
$script:token      = $null
$script:patchTries = @{}
$NEW_CREDENTIAL_ID = $state.newCredentialId
$TRANSIENT_FAILURES = if ($state.PSObject.Properties.Name -contains 'transientFailAttempts') {
    [int]$state.transientFailAttempts
}
else {
    1
}

# Mutable scenario state.
$credentials = [System.Collections.ArrayList]::new()
foreach ($c in $state.credentials) { [void]$credentials.Add($c) }
$adapters = [System.Collections.ArrayList]::new()
foreach ($a in $state.adapters) { [void]$adapters.Add($a) }

if (Test-Path -LiteralPath $LogPath) { Remove-Item -LiteralPath $LogPath -Force }
New-Item -ItemType File -Path $LogPath -Force | Out-Null

function Write-Entry([hashtable]$Entry) {
    $line = $Entry | ConvertTo-Json -Depth 12 -Compress
    Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8
}

function ConvertTo-Query([string]$RawQuery) {
    $q = @{}
    if ([string]::IsNullOrEmpty($RawQuery)) { return $q }
    foreach ($pair in $RawQuery.TrimStart('?').Split('&')) {
        if (-not $pair) { continue }
        $kv = $pair.Split('=', 2)
        $k = [System.Uri]::UnescapeDataString($kv[0])
        $v = if ($kv.Count -gt 1) { [System.Uri]::UnescapeDataString($kv[1]) } else { '' }
        if ($q.ContainsKey($k)) { $q[$k] = @($q[$k]) + $v } else { $q[$k] = $v }
    }
    return $q
}

function Get-BoundAdapterCount([string]$CredentialId) {
    @($adapters | Where-Object { $_.credentialInstanceId -eq $CredentialId }).Count
}

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://127.0.0.1:$Port/")
$listener.Start()

if ($ReadyPath) { Set-Content -LiteralPath $ReadyPath -Value $Port -Encoding utf8 }

try {
    while ($listener.IsListening) {
        $ctx = $listener.GetContext()
        $req = $ctx.Request
        $res = $ctx.Response

        $method = $req.HttpMethod
        # The SDK emits "//suite-api/..."; collapse repeated separators.
        $rawPath = $req.Url.AbsolutePath
        $path    = ($rawPath -replace '/{2,}', '/')
        $query   = ConvertTo-Query $req.Url.Query

        $body = ''
        if ($req.HasEntityBody) {
            $reader = [System.IO.StreamReader]::new($req.InputStream, [System.Text.Encoding]::UTF8)
            $body = $reader.ReadToEnd()
            $reader.Close()
        }

        $apiPath = $path
        if ($apiPath.StartsWith($basePath)) { $apiPath = $apiPath.Substring($basePath.Length) }
        if (-not $apiPath.StartsWith('/')) { $apiPath = '/' + $apiPath }

        $routeKey     = "$method $apiPath"
        $operationId  = $routes[$routeKey]
        $outOfContract = [string]::IsNullOrEmpty($operationId)

        $status    = 200
        $payload   = $null
        $violation = $null

        if ($outOfContract) {
            $status  = 404
            $payload = @{ message = "no such operation in contract: $routeKey" }
        }
        elseif ($operationId -ne 'acquireToken' -and
                $req.Headers['Authorization'] -ne "OpsToken $($script:token)") {
            $status  = 401
            $payload = @{ message = 'missing or stale Authorization header' }
        }
        else {
            $json = $null
            if ($body) { try { $json = $body | ConvertFrom-Json } catch { $json = $null } }

            switch ($operationId) {

                'acquireToken' {
                    if (-not $json -or -not $json.username -or -not $json.password) {
                        $status = 400; $payload = @{ message = 'username and password are required' }
                    }
                    else {
                        $script:token = 'ops-token-2f1c4b'
                        $payload = @{
                            token     = $script:token
                            validity  = 1893456000000
                            expiresAt = 'Tuesday, January 1, 2030 at 12:00:00 AM UTC'
                            roles     = @('Administrator')
                        }
                    }
                }

                'releaseToken' {
                    $script:token = $null
                    $payload = @{}
                }

                'getCurrentVersionOfServer' {
                    $payload = @{
                        releaseName               = 'VCF Operations 9.1.0.0'
                        major                     = 9
                        minor                     = 1
                        minorMinor                = 0
                        patch                     = 0
                        buildNumber               = 24512000
                        releasedDate              = 1772568000000
                        humanlyReadableReleaseDate = 'Tuesday, March 3, 2026 at 12:00:00 PM PST'
                    }
                }

                'getCredentials' {
                    $items = $credentials
                    if ($query.ContainsKey('adapterKind')) {
                        $kinds = @($query['adapterKind'])
                        $items = $items | Where-Object { $kinds -contains $_.adapterKindKey }
                    }
                    $payload = @{ credentialInstances = @($items) }
                }

                'enumerateAdapterInstances' {
                    $items = $adapters
                    if ($query.ContainsKey('adapterKindKey')) {
                        $kind = [string]$query['adapterKindKey']
                        $items = $items | Where-Object { $_.resourceKey.adapterKindKey -eq $kind }
                    }
                    $payload = @{ adapterInstancesInfoDto = @($items) }
                }

                'createCredential' {
                    if (-not $json) {
                        $status = 400; $payload = @{ message = 'body is required' }
                    }
                    elseif ($json.PSObject.Properties.Name -contains 'id') {
                        # Spec: id "should be null for credential instance creation requests".
                        $status = 400
                        $payload = @{ message = 'id must not be supplied when creating a credential' }
                    }
                    elseif (-not $json.name -or -not $json.adapterKindKey -or -not $json.credentialKindKey) {
                        $status = 400
                        $payload = @{ message = 'name, adapterKindKey and credentialKindKey are required' }
                    }
                    else {
                        $created = [ordered]@{
                            id                = $NEW_CREDENTIAL_ID
                            name              = $json.name
                            adapterKindKey    = $json.adapterKindKey
                            credentialKindKey = $json.credentialKindKey
                            editable          = $true
                        }
                        [void]$credentials.Add([pscustomobject]$created)
                        $status  = 201
                        $payload = $created
                    }
                }

                'patchAdapterInstance' {
                    if (-not $json -or -not $json.id) {
                        $status = 400; $payload = @{ message = 'id is required' }
                    }
                    elseif (-not $json.resourceKey) {
                        # resourceKey is the only required property of adapter-instance.
                        $status = 400; $payload = @{ message = 'resourceKey is required' }
                    }
                    else {
                        $target = $adapters | Where-Object { $_.id -eq $json.id } | Select-Object -First 1
                        if (-not $target) {
                            $status = 404; $payload = @{ message = "unknown adapter $($json.id)" }
                        }
                        else {
                            $n = 1 + [int]$script:patchTries[$json.id]
                            $script:patchTries[$json.id] = $n
                            $alwaysFails = (($state.PSObject.Properties.Name -contains 'persistentFailAdapterId') -and
                                            ($state.persistentFailAdapterId -eq $json.id))
                            if ($alwaysFails -or
                                (($state.transientFailAdapterId -eq $json.id) -and
                                 $n -le $TRANSIENT_FAILURES)) {
                                # A busy collector rejects the repoint with the retryable status.
                                $status  = 503
                                $payload = @{ message = 'adapter instance is busy collecting; retry' }
                            }
                            else {
                                if ($json.PSObject.Properties.Name -contains 'credentialInstanceId') {
                                    $target.credentialInstanceId = $json.credentialInstanceId
                                }
                                $payload = $target
                            }
                        }
                    }
                }

                'partialUpdateCredential' {
                    if (-not $json -or -not $json.id) {
                        # Spec: id must have a value for all non-creation requests.
                        $status = 400; $payload = @{ message = 'id is required' }
                    }
                    else {
                        $target = $credentials | Where-Object { $_.id -eq $json.id } | Select-Object -First 1
                        if (-not $target) {
                            $status = 404; $payload = @{ message = "unknown credential $($json.id)" }
                        }
                        else {
                            $bound = Get-BoundAdapterCount $json.id
                            if ($bound -gt 0) {
                                # Mutating a credential that adapters still point at is
                                # exactly the stranding this rotation must avoid.
                                $status    = 409
                                $violation = "partialUpdateCredential on $($json.id) while $bound adapter instance(s) still reference it"
                                $payload   = @{ message = $violation }
                            }
                            else {
                                foreach ($p in $json.PSObject.Properties) {
                                    if ($p.Name -eq 'id') { continue }
                                    $target.PSObject.Properties[$p.Name].Value = $p.Value
                                }
                                $payload = $target
                            }
                        }
                    }
                }
            }
        }

        $script:seq++
        $entry = @{
            seq           = $script:seq
            operationId   = $operationId
            outOfContract = $outOfContract
            method        = $method
            path          = $apiPath
            rawPath       = $rawPath
            query         = $query
            authorization = $req.Headers['Authorization']
            accept        = $req.Headers['Accept']
            userAgent     = $req.Headers['User-Agent']
            contentType   = $req.ContentType
            body          = $body
            status        = $status
        }
        if ($violation) { $entry.violation = $violation }
        Write-Entry $entry

        $bytes = [System.Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 12 -Compress))
        $res.StatusCode      = $status
        $res.ContentType     = 'application/json'
        $res.ContentLength64 = $bytes.Length
        $res.OutputStream.Write($bytes, 0, $bytes.Length)
        $res.OutputStream.Close()
    }
}
finally {
    $listener.Stop()
    $listener.Close()
}
