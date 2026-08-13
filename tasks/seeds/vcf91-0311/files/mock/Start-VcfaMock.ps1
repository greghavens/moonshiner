<#
.SYNOPSIS
    Loopback test double for the five VCF Automation operations in docs/contract.json.

.DESCRIPTION
    Pinned to the contract: the route table, the accepted query parameters and the accepted
    methods are all read out of docs/contract.json at startup. Nothing else is served. A
    request that does not match a contract operation gets 501 and is still written to the
    request log, so a test can prove the client stayed inside the contract.

    Binds 127.0.0.1 only. Contacts nothing.

    Every request is appended to -LogPath as one JSON object per line:
        seq, method, path, rawQuery, query, headers, bodyRaw, statusCode, operationId

    Stop it by terminating the process; there is deliberately no control endpoint, because a
    control endpoint would be an operation the contract does not name.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [int]    $Port,
    [Parameter(Mandatory)] [string] $ContractPath,
    [Parameter(Mandatory)] [string] $LogPath,
    [Parameter(Mandatory)] [string] $ReadyPath
)

$ErrorActionPreference = 'Stop'

$contract = Get-Content -LiteralPath $ContractPath -Raw | ConvertFrom-Json

# ---------------------------------------------------------------------------
# Route table, built from the contract rather than hand-written.
# ---------------------------------------------------------------------------
$routes = foreach ($property in $contract.operations.PSObject.Properties) {
    $op = $property.Value
    # Regex.Escape escapes '{' but leaves '}' alone, so tolerate either form.
    $pattern = '^' + ([regex]::Escape($op.pathTemplate) -replace '\\?\{[A-Za-z0-9_]+\\?\}', '([^/]+)') + '$'
    $names = [regex]::Matches($op.pathTemplate, '\{([A-Za-z0-9_]+)\}') | ForEach-Object { $_.Groups[1].Value }
    [pscustomobject] @{
        OperationId    = $property.Name
        Method         = $op.method
        Regex          = [regex] $pattern
        ParameterNames = @($names)
        AllowedQuery   = @($op.queryParameters | ForEach-Object { $_.name })
        ExpectsBody    = ($null -ne $op.requestBody)
    }
}

# ---------------------------------------------------------------------------
# Seeded appliance state. Fixed identifiers so runs are byte-for-byte repeatable.
# ---------------------------------------------------------------------------
$ORG_ID          = 'b8f2c4d6-1a37-45e9-8c02-9d5f6e7a3b41'
$PROJECT_ID      = 'a4c81f0e-6d52-4b19-9f7c-2e0b8d31c6aa'
$SANDBOX_ID      = '7b3d29c4-8e15-4f6a-b2d0-3c9a1e58f7b2'
$ITEM_SMALL_ID   = '1f9a5c72-3e64-4d81-b0af-58c7d2916e43'
$ITEM_RETIRED_ID = '5c02de91-7a48-4b3f-9e15-6d8b0c4a72f9'
$ITEM_TYPE_ID    = 'com.vmw.blueprint'
$DEPLOYMENT_ID   = 'd51b8e37-9c02-4a6f-8b14-73fe5a90c2d1'
$DB_RESOURCE_ID  = 'e2f47a10-5b83-4c9d-a06e-18f7c3b52d64'
$APP_RESOURCE_ID = '3a6b9d02-c74e-4185-8f2a-b05d1e93c748'
$READY_RESOURCE_ID = '624a187c-9e35-41d0-84b6-2c8fa970e153'
$INFLIGHT_REQ_ID = '9e14f8b3-2d67-40ac-95e8-4b7c0a26d1f5'
$SUCCESS_REQ_ID  = 'f1703e4a-697d-47ea-a2cf-8fbfe7ab1c54'
$STAMP           = '2026-08-11T09:14:22.418Z'

$projects = @(
    [ordered] @{ id = $PROJECT_ID; name = 'eng-platform'; description = 'Platform engineering workloads'; orgId = $ORG_ID }
    [ordered] @{ id = $SANDBOX_ID; name = 'sandbox';      description = 'Scratch project';               orgId = $ORG_ID }
)

# The retired duplicate is listed first on purpose. 'search' is documented as a substring
# match, so a client that trusts the first hit picks the wrong catalog item.
$catalogItems = @(
    [ordered] @{
        id = $ITEM_RETIRED_ID; name = 'Ubuntu 24.04 Small (retired)'
        description = 'Superseded, kept for audit'; isRequestable = $false; bulkRequestLimit = 1
        projectIds = @($PROJECT_ID); type = [ordered] @{ id = $ITEM_TYPE_ID; name = 'VMware Cloud Templates' }
    }
    [ordered] @{
        id = $ITEM_SMALL_ID; name = 'Ubuntu 24.04 Small'
        description = 'Two-tier Ubuntu template'; isRequestable = $true; bulkRequestLimit = 10
        projectIds = @($PROJECT_ID); type = [ordered] @{ id = $ITEM_TYPE_ID; name = 'VMware Cloud Templates' }
    }
)

function New-ResourceRecord {
    param([string] $Id, [string] $Name)
    [ordered] @{
        id = $Id; name = $Name; description = ''
        type = 'Cloud.vSphere.Machine'; state = 'PROVISIONING'; createdAt = $STAMP
        currentRequest = [ordered] @{
            id = $INFLIGHT_REQ_ID; status = 'INPROGRESS'; actionId = 'Deployment.Create'
            completedTasks = 3; totalTasks = 7
        }
        properties = [ordered] @{ resourceName = $Name; powerState = 'OFF' }
    }
}
$resources = @(
    (New-ResourceRecord -Id $APP_RESOURCE_ID -Name 'app-node')
    (New-ResourceRecord -Id $DB_RESOURCE_ID  -Name 'db-node')
    [ordered] @{
        id = $READY_RESOURCE_ID; name = 'ready-node'; description = ''
        type = 'Cloud.vSphere.Machine'; state = 'ON'; createdAt = $STAMP
        properties = [ordered] @{ resourceName = 'ready-node'; powerState = 'ON' }
    }
)

function New-Page {
    param(
        [object[]] $Content,
        [int] $Size = 20,
        [int] $Number = 0,
        [int] $TotalElements = -1
    )
    $items = @($Content)
    if ($TotalElements -lt 0) { $TotalElements = $items.Count }
    $totalPages = if ($TotalElements -eq 0) { 0 } else { [int] [Math]::Ceiling($TotalElements / [double] $Size) }
    [ordered] @{
        content          = $items
        empty            = ($items.Count -eq 0)
        first            = ($Number -eq 0)
        last             = ($totalPages -eq 0 -or $Number -ge ($totalPages - 1))
        number           = $Number
        numberOfElements = $items.Count
        size             = $Size
        totalElements    = $TotalElements
        totalPages       = $totalPages
    }
}

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
function Invoke-Handler {
    param([string] $OperationId, [hashtable] $PathValues, [hashtable] $Query, [object] $Body)

    switch ($OperationId) {

        'getAllProjects' {
            # Deliberately cap this collection at one record per page even when the client
            # requests a larger size. That makes the double exercise the documented paging
            # contract without requiring hundreds of inert fixture records.
            $pageNumber = if ($Query.ContainsKey('page')) { [int] $Query['page'] } else { 0 }
            $pageItems = @($projects | Select-Object -Skip $pageNumber -First 1)
            return @{
                Status = 200
                Payload = (New-Page -Content $pageItems -Size 1 -Number $pageNumber -TotalElements $projects.Count)
            }
        }

        'getCatalogItems' {
            $hits = $catalogItems
            if ($Query.ContainsKey('projects')) {
                $wanted = ($Query['projects'] -split ',')
                $hits = @($hits | Where-Object { @($_.projectIds | Where-Object { $wanted -contains $_ }).Count -gt 0 })
            }
            if ($Query.ContainsKey('search')) {
                $needle = $Query['search']
                $hits = @($hits | Where-Object { $_.name -like "*$needle*" -or $_.description -like "*$needle*" })
            }
            return @{ Status = 200; Payload = (New-Page -Content $hits) }
        }

        'requestCatalogItemInstances' {
            $itemId = $PathValues['id']
            $item = $catalogItems | Where-Object { $_.id -eq $itemId }
            if (-not $item) {
                return @{ Status = 404; Payload = [ordered] @{ statusCode = 404; message = "Catalog item $itemId does not exist." } }
            }
            if (-not $item.isRequestable) {
                return @{ Status = 400; Payload = [ordered] @{ statusCode = 400; message = "Catalog item '$($item.name)' is not requestable." } }
            }
            $name = if ($Body -and $Body.PSObject.Properties['deploymentName']) { [string] $Body.deploymentName } else { 'unnamed' }
            $count = if ($Body -and $Body.PSObject.Properties['bulkRequestCount']) { [int] $Body.bulkRequestCount } else { 1 }
            $payload = @(
                for ($i = 0; $i -lt [Math]::Max(1, $count); $i++) {
                    [ordered] @{
                        deploymentId   = if ($i -eq 0) { $DEPLOYMENT_ID } else { "$DEPLOYMENT_ID-$i" }
                        deploymentName = if ($i -eq 0) { $name } else { "$name-$i" }
                    }
                }
            )
            return @{ Status = 200; Payload = $payload }
        }

        'getDeploymentResources' {
            if ($PathValues['deploymentId'] -ne $DEPLOYMENT_ID) {
                return @{ Status = 404; Payload = [ordered] @{ statusCode = 404; message = "Deployment $($PathValues['deploymentId']) not found." } }
            }
            $hits = $resources
            if ($Query.ContainsKey('names')) {
                $wanted = ($Query['names'] -split ',')
                $hits = @($hits | Where-Object { $wanted -contains $_.name })
            }
            return @{ Status = 200; Payload = (New-Page -Content $hits) }
        }

        'submitResourceActionRequest' {
            if ($PathValues['deploymentId'] -ne $DEPLOYMENT_ID) {
                return @{ Status = 404; Payload = [ordered] @{ statusCode = 404; message = "Deployment $($PathValues['deploymentId']) not found." } }
            }
            $resource = $resources | Where-Object { $_.id -eq $PathValues['resourceId'] }
            if (-not $resource) {
                return @{ Status = 404; Payload = [ordered] @{ statusCode = 404; message = "Resource $($PathValues['resourceId']) not found in deployment $DEPLOYMENT_ID." } }
            }
            if ($resource.id -eq $READY_RESOURCE_ID) {
                return @{
                    Status  = 200
                    Payload = [ordered] @{
                        id           = $SUCCESS_REQ_ID
                        actionId     = if ($Body -and $Body.PSObject.Properties['actionId']) { [string] $Body.actionId } else { '' }
                        status       = 'CREATED'
                        deploymentId = $DEPLOYMENT_ID
                        resourceIds  = @($READY_RESOURCE_ID)
                    }
                }
            }
            # The deployment this test double hands out is still provisioning, which is the
            # 409 'request state conflict' the reference documents for this operation.
            $action = if ($Body -and $Body.PSObject.Properties['actionId']) { [string] $Body.actionId } else { '<none>' }
            return @{
                Status  = 409
                Payload = [ordered] @{
                    statusCode = 409
                    message    = "Action '$action' cannot be submitted while request $INFLIGHT_REQ_ID is in progress on resource '$($resource.name)'."
                }
            }
        }

        default {
            return @{ Status = 500; Payload = [ordered] @{ statusCode = 500; message = "No handler for $OperationId." } }
        }
    }
}

# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------
function ConvertFrom-RawQuery {
    param([string] $Raw)
    $result = @{}
    if ([string]::IsNullOrEmpty($Raw)) { return $result }
    foreach ($pair in $Raw.TrimStart('?').Split('&')) {
        if ([string]::IsNullOrEmpty($pair)) { continue }
        $idx = $pair.IndexOf('=')
        if ($idx -lt 0) { $result[[uri]::UnescapeDataString($pair)] = '' }
        else {
            $key = [uri]::UnescapeDataString($pair.Substring(0, $idx))
            $result[$key] = [uri]::UnescapeDataString($pair.Substring($idx + 1))
        }
    }
    $result
}

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://127.0.0.1:$Port/")
$listener.Start()

Set-Content -LiteralPath $LogPath -Value '' -NoNewline
Set-Content -LiteralPath $ReadyPath -Value "$Port" -NoNewline

$seq = 0
try {
    while ($listener.IsListening) {
        $context  = $listener.GetContext()
        $request  = $context.Request
        $response = $context.Response
        $seq++

        $path     = $request.Url.AbsolutePath
        $rawQuery = $request.Url.Query
        $query    = ConvertFrom-RawQuery -Raw $rawQuery

        $bodyRaw = ''
        if ($request.HasEntityBody) {
            $reader  = [System.IO.StreamReader]::new($request.InputStream, $request.ContentEncoding)
            $bodyRaw = $reader.ReadToEnd()
            $reader.Dispose()
        }

        $operationId = $null
        $status      = 501
        $payload     = [ordered] @{
            statusCode = 501
            message    = "This test double is pinned to docs/contract.json and serves only the operations it names. $($request.HttpMethod) $path is not one of them."
        }

        $route = $routes | Where-Object { $_.Method -eq $request.HttpMethod -and $_.Regex.IsMatch($path) } | Select-Object -First 1
        if ($route) {
            $operationId = $route.OperationId

            $undocumented = @($query.Keys | Where-Object { $route.AllowedQuery -notcontains $_ })
            $parsedBody   = $null
            $bodyError    = $null
            if ($route.ExpectsBody) {
                if ([string]::IsNullOrWhiteSpace($bodyRaw)) { $bodyError = 'a JSON body is required' }
                else {
                    try { $parsedBody = ConvertFrom-Json -InputObject $bodyRaw }
                    catch { $bodyError = 'the body is not valid JSON' }
                    if (-not $bodyError -and $request.ContentType -notmatch '^application/json') {
                        $bodyError = "Content-Type must be application/json, got '$($request.ContentType)'"
                    }
                }
            }

            if ($undocumented.Count -gt 0) {
                $status  = 400
                $payload = [ordered] @{
                    statusCode = 400
                    message    = ("Query parameter(s) '" + ($undocumented -join "', '") +
                                  "' are not documented for $operationId. Documented: " +
                                  (($route.AllowedQuery | Sort-Object) -join ', ') + '.')
                }
            }
            elseif ($bodyError) {
                $status  = 400
                $payload = [ordered] @{ statusCode = 400; message = "Invalid request for ${operationId}: $bodyError." }
            }
            else {
                $pathValues = @{}
                if ($route.ParameterNames.Count -gt 0) {
                    $groups = $route.Regex.Match($path).Groups
                    for ($i = 0; $i -lt $route.ParameterNames.Count; $i++) {
                        $pathValues[$route.ParameterNames[$i]] = [uri]::UnescapeDataString($groups[$i + 1].Value)
                    }
                }
                $outcome = Invoke-Handler -OperationId $operationId -PathValues $pathValues -Query $query -Body $parsedBody
                $status  = $outcome.Status
                $payload = $outcome.Payload
            }
        }

        $entry = [ordered] @{
            seq         = $seq
            method      = $request.HttpMethod
            path        = $path
            rawQuery    = $rawQuery
            query       = $query
            headers     = [ordered] @{
                authorization = [string] $request.Headers['Authorization']
                contentType   = [string] $request.ContentType
                accept        = [string] $request.Headers['Accept']
            }
            bodyRaw     = $bodyRaw
            statusCode  = $status
            operationId = $operationId
        }
        Add-Content -LiteralPath $LogPath -Value (ConvertTo-Json -InputObject $entry -Depth 12 -Compress)

        $bytes = [System.Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $payload -Depth 20))
        $response.StatusCode      = $status
        $response.ContentType     = 'application/json'
        $response.ContentLength64 = $bytes.Length
        $response.OutputStream.Write($bytes, 0, $bytes.Length)
        $response.OutputStream.Close()
    }
}
finally {
    if ($listener.IsListening) { $listener.Stop() }
    $listener.Close()
}
