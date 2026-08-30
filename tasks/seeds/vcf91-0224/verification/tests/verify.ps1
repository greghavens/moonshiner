<#
.SYNOPSIS
    Protected verifier for the VcfSddcLcm module.

.DESCRIPTION
    Drives src/VcfSddcLcm against a deterministic in-process transport double
    pinned to docs/contract.json, then asserts the exact shape of every request.
    The verifier opens no sockets and never contacts a VMware endpoint.

.NOTES
    Solve the task in src/VcfSddcLcm/VcfSddcLcm.psm1. This file, docs/, mock/,
    tools/, README.md, and the module manifest are protected.
#>
[CmdletBinding()]
param(
    [string] $ResultPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root       = Split-Path -Parent $PSScriptRoot
$modulePath = Join-Path $root 'src/VcfSddcLcm/VcfSddcLcm.psd1'
$scriptModulePath = Join-Path $root 'src/VcfSddcLcm/VcfSddcLcm.psm1'
$contractPath = Join-Path $root 'docs/contract.json'
$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json

$script:Checks = [System.Collections.Generic.List[object]]::new()

function Assert-That {
    param(
        [Parameter(Mandatory)][string] $Name,
        [Parameter(Mandatory)][bool]   $Condition,
        [string] $Expected,
        [string] $Actual
    )
    $script:Checks.Add([pscustomobject]@{
        name = $Name; passed = $Condition; expected = $Expected; actual = $Actual
    })
    if ($Condition) {
        Write-Host ("  PASS  " + $Name) -ForegroundColor Green
    }
    else {
        Write-Host ("  FAIL  " + $Name) -ForegroundColor Red
        if ($Expected) { Write-Host ("          expected: " + $Expected) -ForegroundColor DarkGray }
        if ($Actual)   { Write-Host ("          actual:   " + $Actual)   -ForegroundColor DarkGray }
    }
}

function Get-Header {
    param($Entry, [string]$Name)
    foreach ($p in $Entry.headers.PSObject.Properties) {
        if ($p.Name -ieq $Name) { return $p.Value }
    }
    $null
}

function ConvertFrom-RawQuery {
    param([string]$RawQuery)
    $out = [ordered]@{}
    if ([string]::IsNullOrEmpty($RawQuery)) { return $out }
    foreach ($pair in $RawQuery.TrimStart('?').Split('&')) {
        if (-not $pair) { continue }
        $i = $pair.IndexOf('=')
        if ($i -lt 0) { $out[[uri]::UnescapeDataString($pair)] = $null }
        else {
            $out[[uri]::UnescapeDataString($pair.Substring(0, $i))] =
                [uri]::UnescapeDataString($pair.Substring($i + 1))
        }
    }
    $out
}

function Get-Log {
    @($global:VcfSddcLcmVerifierRequests)
}

# The transport state deliberately uses a one-item task page. A pre-existing
# unrelated task places the first submitted task on page 1 during the retry,
# proving that a solution cannot stop after the default page.
$token       = 'mock-sddc-lcm-token'
$componentId = '7c9e6679-7425-40de-944b-e07fc1f90ae7'
$keyA        = 'bundle-key-alpha'
$keyB        = 'bundle-key-bravo'

$global:VcfSddcLcmVerifierContract = $contract
$global:VcfSddcLcmVerifierToken = $token
$global:VcfSddcLcmVerifierRequests = [System.Collections.Generic.List[object]]::new()
$global:VcfSddcLcmVerifierTasks = [ordered]@{}
$global:VcfSddcLcmVerifierBundles = [ordered]@{}
$global:VcfSddcLcmVerifierSequence = 0

$decoyId = 'd0000000-0000-4000-8000-000000000000'
$global:VcfSddcLcmVerifierTasks[$decoyId] = [ordered]@{
    id = $decoyId; status = 'SUCCEEDED'; resourceId = $componentId
    resourceType = 'COMPONENT'; correlationId = 'unrelated-correlation-id'
    createTime = '2026-01-15T09:00:00.000Z'
}

# A global function has command precedence over the built-in cmdlet when the
# module invokes Invoke-RestMethod. It records the exact splatted parameters and
# serves only routes read from the protected contract.
function global:Invoke-RestMethod {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][uri] $Uri,
        [Parameter(Mandatory)][string] $Method,
        [hashtable] $Headers,
        [string] $Body,
        [string] $ContentType
    )

    $methodName = $Method.ToUpperInvariant()
    $matchedOperation = $null
    $routeValues = @{}
    foreach ($property in $global:VcfSddcLcmVerifierContract.operations.PSObject.Properties) {
        $operation = $property.Value
        if ($methodName -ne $operation.method) { continue }
        $pattern = '^' + ([regex]::Escape($operation.path) -replace '\\?\{(\w+)\\?\}', '(?<$1>[^/]+)') + '$'
        $match = [regex]::Match($Uri.AbsolutePath, $pattern)
        if (-not $match.Success) { continue }
        $matchedOperation = $operation
        foreach ($groupName in ([regex]$pattern).GetGroupNames()) {
            if ($groupName -notmatch '^\d+$') {
                $routeValues[$groupName] = $match.Groups[$groupName].Value
            }
        }
        break
    }

    $capturedHeaders = [ordered]@{}
    if ($Headers) {
        foreach ($name in $Headers.Keys) { $capturedHeaders[$name] = $Headers[$name] }
    }
    if ($PSBoundParameters.ContainsKey('ContentType')) {
        $capturedHeaders['Content-Type'] = $ContentType
    }

    $query = [ordered]@{}
    if ($Uri.Query) {
        foreach ($pair in $Uri.Query.TrimStart('?').Split('&')) {
            if (-not $pair) { continue }
            $i = $pair.IndexOf('=')
            if ($i -lt 0) { $query[[uri]::UnescapeDataString($pair)] = $null }
            else {
                $query[[uri]::UnescapeDataString($pair.Substring(0, $i))] =
                    [uri]::UnescapeDataString($pair.Substring($i + 1))
            }
        }
    }

    $status = if ($matchedOperation -and $matchedOperation.operationId -eq 'generateComponentSupportBundle') { 202 } else { 200 }
    $entry = [pscustomobject]@{
        seq         = $global:VcfSddcLcmVerifierRequests.Count
        method      = $methodName
        path        = $Uri.AbsolutePath
        rawQuery    = $Uri.Query
        matched     = [bool]$matchedOperation
        operationId = if ($matchedOperation) { $matchedOperation.operationId } else { $null }
        status      = $status
        headers     = [pscustomobject]$capturedHeaders
        body        = if ($PSBoundParameters.ContainsKey('Body')) { $Body } else { '' }
        hasBody     = $PSBoundParameters.ContainsKey('Body')
    }
    $global:VcfSddcLcmVerifierRequests.Add($entry)

    if (-not $matchedOperation) {
        throw "No contract operation serves $methodName $($Uri.AbsolutePath)."
    }
    if ($Headers['Authorization'] -ne "Bearer $global:VcfSddcLcmVerifierToken") {
        $entry.status = 401
        throw 'Missing or invalid bearer Authorization header.'
    }

    switch ($matchedOperation.operationId) {
        'getTasks' {
            $items = @($global:VcfSddcLcmVerifierTasks.Values | Where-Object {
                (-not $query['resourceId'] -or $_['resourceId'] -eq $query['resourceId']) -and
                (-not $query['resourceType'] -or $_['resourceType'] -eq $query['resourceType'])
            })
            $pageNumber = if ($query.Contains('pageNumber')) { [int]$query['pageNumber'] } else { 0 }
            $totalPages = [Math]::Max(1, $items.Count)
            $elements = @()
            if ($pageNumber -ge 0 -and $pageNumber -lt $items.Count) {
                $elements = @([pscustomobject]$items[$pageNumber])
            }
            return [pscustomobject]@{
                elements = $elements
                pageMetadata = [pscustomobject]@{
                    pageNumber = $pageNumber; pageSize = 1
                    totalElements = $items.Count; totalPages = $totalPages
                }
            }
        }

        'generateComponentSupportBundle' {
            $global:VcfSddcLcmVerifierSequence++
            $taskId = 'a0000000-0000-4000-8000-{0}' -f $global:VcfSddcLcmVerifierSequence.ToString('000000000000')
            $task = [ordered]@{
                id = $taskId; status = 'RUNNING'
                resourceId = $routeValues['componentId']; resourceType = 'COMPONENT'
                correlationId = $Headers['X-Correlation-Id']
                createTime = '2026-01-15T09:00:01.000Z'
            }
            $global:VcfSddcLcmVerifierTasks[$taskId] = $task
            return [pscustomobject]$task
        }

        'getTask' {
            $taskId = $routeValues['taskId']
            if (-not $global:VcfSddcLcmVerifierTasks.Contains($taskId)) {
                $entry.status = 404
                throw "No task $taskId."
            }
            $task = $global:VcfSddcLcmVerifierTasks[$taskId]
            if ($task['status'] -eq 'RUNNING') {
                $task['status'] = 'SUCCEEDED'
                $global:VcfSddcLcmVerifierSequence++
                $bundleId = 'b0000000-0000-4000-8000-{0}' -f $global:VcfSddcLcmVerifierSequence.ToString('000000000000')
                $global:VcfSddcLcmVerifierBundles[$bundleId] = [ordered]@{
                    id = $bundleId; name = 'support-bundle.tgz'; size = 10485760
                    createdTimestamp = '2026-01-15T09:00:02.000Z'
                    url = "https://mock.invalid/downloads/$bundleId.tgz"
                    componentId = $task['resourceId']
                }
            }
            return [pscustomobject]$task
        }

        'getComponentSupportBundles' {
            $items = foreach ($bundle in $global:VcfSddcLcmVerifierBundles.Values) {
                if ($bundle['componentId'] -ne $routeValues['componentId']) { continue }
                [pscustomobject]@{
                    id = $bundle['id']; name = $bundle['name']; size = $bundle['size']
                    createdTimestamp = $bundle['createdTimestamp']; url = $bundle['url']
                }
            }
            return @($items)
        }
    }
}

try {
    Write-Host "`n[module]" -ForegroundColor Cyan

    Assert-That -Name 'module manifest src/VcfSddcLcm/VcfSddcLcm.psd1 exists' `
        -Condition (Test-Path $modulePath)

    $manifest = $null
    try { $manifest = Import-PowerShellDataFile $modulePath } catch { }
    $required = @()
    if ($manifest -and $manifest.ContainsKey('RequiredModules')) {
        $required = @($manifest.RequiredModules | ForEach-Object {
            if ($_ -is [hashtable]) { $_.ModuleName } else { "$_" }
        })
    }
    Assert-That -Name 'manifest requires VMware.Sdk.Vcf.SddcManager (SDK is a prerequisite, not vendored)' `
        -Condition ($required -contains 'VMware.Sdk.Vcf.SddcManager') `
        -Expected 'VMware.Sdk.Vcf.SddcManager in RequiredModules' -Actual ($required -join ', ')

    Assert-That -Name 'no vendored copy of the VMware SDK in the repo' `
        -Condition (-not (Get-ChildItem -Path $root -Recurse -Directory -Filter 'VMware.Sdk.Vcf*' -ErrorAction SilentlyContinue))

    # Import the script module directly. The manifest is inspected above, while
    # the published SDK is an environment prerequisite rather than verifier
    # logic; the implementation only consumes the SDK connection object's
    # documented ServiceUri/SessionSecret surface.
    Import-Module $scriptModulePath -Force -ErrorAction Stop
    $exported = (Get-Module VcfSddcLcm).ExportedFunctions.Keys
    Assert-That -Name 'exports New-VcfSddcLcmSession' -Condition ($exported -contains 'New-VcfSddcLcmSession')
    Assert-That -Name 'exports Start-VcfSddcLcmSupportBundle' -Condition ($exported -contains 'Start-VcfSddcLcmSupportBundle')

    $session = New-VcfSddcLcmSession -Server 'https://mock.invalid:8443/ignored/path' -Token $token
    Assert-That -Name 'explicit session keeps only the server scheme and authority' `
        -Condition ($session.BaseUri -eq 'https://mock.invalid:8443') `
        -Expected 'https://mock.invalid:8443' -Actual "$($session.BaseUri)"
    Assert-That -Name 'explicit session retains the bearer token' `
        -Condition ($session.Token -eq $token) -Expected $token -Actual "$($session.Token)"

    $sdkConnection = [pscustomobject]@{
        ServiceUri = [uri]'https://sdk-connection.invalid/v1/sddc-manager'
        SessionSecret = 'sdk-session-secret'
    }
    $sdkSession = New-VcfSddcLcmSession -Connection $sdkConnection
    Assert-That -Name 'SDK session reads the authority from ServiceUri' `
        -Condition ($sdkSession.BaseUri -eq 'https://sdk-connection.invalid') `
        -Expected 'https://sdk-connection.invalid' -Actual "$($sdkSession.BaseUri)"
    Assert-That -Name 'SDK session reads the bearer token from SessionSecret' `
        -Condition ($sdkSession.Token -eq 'sdk-session-secret') `
        -Expected 'sdk-session-secret' -Actual "$($sdkSession.Token)"

    # =====================================================================
    # A. First submission, -LookBackWindow omitted
    # =====================================================================
    Write-Host "`n[A] first submission (optional field unset)" -ForegroundColor Cyan
    $r1 = Start-VcfSddcLcmSupportBundle -Session $session -ComponentId $componentId -CorrelationId $keyA

    Assert-That -Name 'A: reports the task as newly created (Reused = false)' `
        -Condition (-not $r1.Reused) -Expected 'False' -Actual "$($r1.Reused)"
    Assert-That -Name 'A: returns a task id' -Condition ([bool]$r1.Task.id) -Actual "$($r1.Task.id)"

    $log   = Get-Log
    $posts = @($log | Where-Object { $_.operationId -eq 'generateComponentSupportBundle' })
    $lists = @($log | Where-Object { $_.operationId -eq 'getTasks' })

    Assert-That -Name 'A: exactly one POST issued' -Condition ($posts.Count -eq 1) `
        -Expected '1' -Actual "$($posts.Count)"
    Assert-That -Name 'A: checked getTasks before submitting' `
        -Condition ($lists.Count -eq 1 -and $log[0].operationId -eq 'getTasks') `
        -Expected 'one getTasks request first' -Actual "$($log[0].operationId)"

    $post = $posts[0]
    Assert-That -Name 'A: POST targets the contract path' `
        -Condition ($post.path -eq "/sddc-lcm/v1/components/$componentId/support-bundles") `
        -Expected "/sddc-lcm/v1/components/$componentId/support-bundles" -Actual $post.path
    Assert-That -Name 'A: POST carries no query string' `
        -Condition ([string]::IsNullOrEmpty($post.rawQuery)) -Expected '(empty)' -Actual "$($post.rawQuery)"

    Assert-That -Name 'A: request body is exactly {} when -LookBackWindow is omitted' `
        -Condition ($post.body -eq '{}') -Expected '{}' -Actual "$($post.body)"
    Assert-That -Name 'A: body does not mention lookBackWindow at all' `
        -Condition ($post.body -notmatch 'lookBackWindow') -Expected 'no lookBackWindow key' -Actual "$($post.body)"
    $postObj = $post.body | ConvertFrom-Json
    Assert-That -Name 'A: body has zero properties' `
        -Condition (@($postObj.PSObject.Properties).Count -eq 0) `
        -Expected '0' -Actual "$(@($postObj.PSObject.Properties).Count)"

    Assert-That -Name 'A: POST sends X-Correlation-Id' `
        -Condition ((Get-Header $post 'X-Correlation-Id') -eq $keyA) `
        -Expected $keyA -Actual "$(Get-Header $post 'X-Correlation-Id')"
    Assert-That -Name 'A: POST sends bearer Authorization' `
        -Condition ((Get-Header $post 'Authorization') -eq "Bearer $token") `
        -Expected "Bearer $token" -Actual "$(Get-Header $post 'Authorization')"
    Assert-That -Name 'A: POST sends application/json content type' `
        -Condition ("$(Get-Header $post 'Content-Type')" -eq 'application/json') `
        -Expected 'application/json' -Actual "$(Get-Header $post 'Content-Type')"

    $q = ConvertFrom-RawQuery $lists[0].rawQuery
    Assert-That -Name 'A: initial getTasks sends exactly resourceId and resourceType' `
        -Condition (@($q.Keys).Count -eq 2 -and $q.Contains('resourceId') -and $q.Contains('resourceType')) `
        -Expected 'resourceId, resourceType' -Actual (@($q.Keys) -join ', ')
    Assert-That -Name 'A: getTasks resourceId is the component id' `
        -Condition ($q['resourceId'] -eq $componentId) -Expected $componentId -Actual "$($q['resourceId'])"
    Assert-That -Name 'A: getTasks sends no empty-valued query parameters' `
        -Condition ($lists[0].rawQuery -notmatch '=(&|$)') -Actual "$($lists[0].rawQuery)"

    # =====================================================================
    # B. Retry: the matching task is now on page 1
    # =====================================================================
    Write-Host "`n[B] retry with the same correlation id on a later page" -ForegroundColor Cyan
    $beforeRetry = $global:VcfSddcLcmVerifierRequests.Count
    $r2 = Start-VcfSddcLcmSupportBundle -Session $session -ComponentId $componentId -CorrelationId $keyA

    $log = Get-Log
    $retryLog = @($log | Select-Object -Skip $beforeRetry)
    $retryLists = @($retryLog | Where-Object operationId -eq 'getTasks')
    $posts = @($log | Where-Object operationId -eq 'generateComponentSupportBundle')

    Assert-That -Name 'B: searched both task pages before adopting' `
        -Condition ($retryLists.Count -eq 2) -Expected '2 getTasks requests' -Actual "$($retryLists.Count)"
    $pageOneQuery = ConvertFrom-RawQuery $retryLists[-1].rawQuery
    Assert-That -Name 'B: requested pageNumber=1 only when the later page was needed' `
        -Condition ($pageOneQuery['pageNumber'] -eq '1' -and @($pageOneQuery.Keys).Count -eq 3) `
        -Expected 'resourceId, resourceType, pageNumber=1' -Actual "$($retryLists[-1].rawQuery)"
    Assert-That -Name 'B: still exactly one POST after the retry (no duplicate bundle)' `
        -Condition ($posts.Count -eq 1) -Expected '1' -Actual "$($posts.Count)"
    Assert-That -Name 'B: reports the task as adopted (Reused = true)' `
        -Condition ([bool]$r2.Reused) -Expected 'True' -Actual "$($r2.Reused)"
    Assert-That -Name 'B: returns the same task id as the first call' `
        -Condition ($r2.Task.id -eq $r1.Task.id) -Expected "$($r1.Task.id)" -Actual "$($r2.Task.id)"

    # =====================================================================
    # C. New correlation id, optional field SET, and -Wait
    # =====================================================================
    Write-Host "`n[C] new correlation id with -LookBackWindow and -Wait" -ForegroundColor Cyan
    $r3 = Start-VcfSddcLcmSupportBundle -Session $session -ComponentId $componentId `
        -CorrelationId $keyB -LookBackWindow 24 -Wait -PollIntervalSeconds 0 -TimeoutSeconds 60

    $log = Get-Log
    $posts = @($log | Where-Object operationId -eq 'generateComponentSupportBundle')
    Assert-That -Name 'C: a different correlation id does submit a new request' `
        -Condition ($posts.Count -eq 2) -Expected '2' -Actual "$($posts.Count)"

    $post2 = $posts[-1]
    Assert-That -Name 'C: body is exactly {"lookBackWindow":24} when supplied' `
        -Condition ($post2.body -eq '{"lookBackWindow":24}') `
        -Expected '{"lookBackWindow":24}' -Actual "$($post2.body)"
    $obj2 = $post2.body | ConvertFrom-Json
    Assert-That -Name 'C: body has exactly one property' `
        -Condition (@($obj2.PSObject.Properties).Count -eq 1) `
        -Expected '1' -Actual "$(@($obj2.PSObject.Properties).Count)"
    Assert-That -Name 'C: lookBackWindow serialised as a JSON number, not a string' `
        -Condition ($obj2.lookBackWindow -is [int] -or $obj2.lookBackWindow -is [long]) `
        -Expected 'integer' -Actual "$($obj2.lookBackWindow.GetType().Name)"
    Assert-That -Name 'C: POST sends the second correlation id' `
        -Condition ((Get-Header $post2 'X-Correlation-Id') -eq $keyB) `
        -Expected $keyB -Actual "$(Get-Header $post2 'X-Correlation-Id')"

    Assert-That -Name 'C: polled getTask while waiting' `
        -Condition (@($log | Where-Object operationId -eq 'getTask').Count -ge 1)
    Assert-That -Name 'C: task reached SUCCEEDED' `
        -Condition ($r3.Task.status -eq 'SUCCEEDED') -Expected 'SUCCEEDED' -Actual "$($r3.Task.status)"
    Assert-That -Name 'C: resolved the bundle via getComponentSupportBundles' `
        -Condition (@($log | Where-Object operationId -eq 'getComponentSupportBundles').Count -ge 1)
    Assert-That -Name 'C: returned a support bundle' `
        -Condition ([bool]$r3.SupportBundle -and [bool]$r3.SupportBundle.id) `
        -Actual "$($r3.SupportBundle.id)"

    # =====================================================================
    # D. Global contract and wire hygiene
    # =====================================================================
    Write-Host "`n[D] global" -ForegroundColor Cyan
    $log = Get-Log
    $allowed = $contract.operations.PSObject.Properties.Name

    $offContract = @($log | Where-Object { -not $_.matched })
    Assert-That -Name 'D: never called an endpoint outside the contract' `
        -Condition ($offContract.Count -eq 0) -Expected '0' `
        -Actual (($offContract | ForEach-Object { "$($_.method) $($_.path)" }) -join '; ')

    $unknownOps = @($log | Where-Object { $_.operationId -and $_.operationId -notin $allowed })
    Assert-That -Name 'D: every request maps to a contract operationId' `
        -Condition ($unknownOps.Count -eq 0) -Expected '0' -Actual "$($unknownOps.Count)"

    $unauth = @($log | Where-Object { (Get-Header $_ 'Authorization') -ne "Bearer $token" })
    Assert-That -Name 'D: every request carries bearer Authorization' `
        -Condition ($unauth.Count -eq 0) -Expected '0' -Actual "$($unauth.Count)"

    $badTaskQueries = @($log | Where-Object operationId -eq 'getTasks' | Where-Object {
        $query = ConvertFrom-RawQuery $_.rawQuery
        -not $query.Contains('resourceId') -or -not $query.Contains('resourceType') -or
        @($query.Keys | Where-Object { $_ -notin 'resourceId','resourceType','pageNumber' }).Count -gt 0
    })
    Assert-That -Name 'D: getTasks uses only component filters and required pageNumber' `
        -Condition ($badTaskQueries.Count -eq 0) -Expected '0' -Actual "$($badTaskQueries.Count)"

    $getWithEntity = @($log | Where-Object { $_.method -eq 'GET' -and ($_.hasBody -or (Get-Header $_ 'Content-Type')) })
    Assert-That -Name 'D: GET requests have no body or Content-Type header' `
        -Condition ($getWithEntity.Count -eq 0) -Expected '0' -Actual "$($getWithEntity.Count)"

    Assert-That -Name 'D: total POSTs is 2 (one per correlation id)' `
        -Condition (@($log | Where-Object operationId -eq 'generateComponentSupportBundle').Count -eq 2) `
        -Expected '2' `
        -Actual "$(@($log | Where-Object operationId -eq 'generateComponentSupportBundle').Count)"
}
catch {
    Assert-That -Name 'verifier completed without an unhandled error' `
        -Condition $false -Expected 'no exception' -Actual $_.Exception.Message
    Write-Host ("`nUnhandled error: " + $_.Exception.Message) -ForegroundColor Red
    if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray }
}
finally {
    Remove-Module VcfSddcLcm -Force -ErrorAction SilentlyContinue
    Remove-Item Function:\global:Invoke-RestMethod -Force -ErrorAction SilentlyContinue
    Remove-Variable -Scope Global -Name VcfSddcLcmVerifierContract,VcfSddcLcmVerifierToken,`
        VcfSddcLcmVerifierRequests,VcfSddcLcmVerifierTasks,VcfSddcLcmVerifierBundles,`
        VcfSddcLcmVerifierSequence -Force -ErrorAction SilentlyContinue
}

$passed = @($script:Checks | Where-Object passed).Count
$total  = $script:Checks.Count
Write-Host ("`n{0}/{1} checks passed" -f $passed, $total) `
    -ForegroundColor $(if ($passed -eq $total) { 'Green' } else { 'Red' })

if ($ResultPath) {
    [pscustomobject]@{
        passed = $passed; total = $total
        success = ($passed -eq $total); checks = $script:Checks
    } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ResultPath
}

if ($passed -ne $total) {
    Write-Host "`nFailed checks:" -ForegroundColor Red
    $script:Checks | Where-Object { -not $_.passed } | ForEach-Object { Write-Host "  - $($_.name)" }
    exit 1
}
exit 0
