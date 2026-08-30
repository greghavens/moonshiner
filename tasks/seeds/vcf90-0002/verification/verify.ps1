#Requires -Version 7.0
<#
    Acceptance harness for the VCF 9.0.0.0 credential rotation module.

    Starts the contract-pinned loopback fixture on 127.0.0.1, drives the
    candidate module against it, then grades the recorded request log. No live
    VMware endpoint is contacted and no VMware module is vendored: the genuine
    VMware.Sdk.Vcf.SddcManager package is supplied by the environment.

    This file is protected. Do not modify it.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$modulePath = Join-Path $root 'VcfCredentialRotation.psm1'

$script:failures = [System.Collections.Generic.List[string]]::new()
function Fail([string]$message) { $script:failures.Add($message) | Out-Null }
function Check([bool]$condition, [string]$message) {
    if (-not $condition) { Fail $message }
}

# Values the fixture pins. Kept in sync with mock_sddc.py.
$expectedRefreshTokenId = 'vcf90-refresh-token-0001'
$initialAccessToken = 'vcf90-access-token-initial'
$refreshedAccessToken = 'vcf90-access-token-refreshed'
$expectedRotatedIds = @('host-01', 'host-02', 'host-03', 'host-04')
$forbiddenQueryKeys = @('resourceName', 'resourceIp', 'domainName', 'accountType')
$allowedQueryKeys = @('resourceType', 'pageNumber', 'pageSize')

function Get-Keys($object) {
    if ($null -eq $object) { return @() }
    return @($object.PSObject.Properties.Name | Sort-Object)
}

function Test-HasKey($object, [string]$name) {
    if ($null -eq $object) { return $false }
    return [bool]($object.PSObject.Properties.Name -contains $name)
}

# Walk a decoded request body and report every null or blank leaf. The pinned
# contract requires unset optional fields to be absent, never sent empty.
function Find-EmptyLeaf($node, [string]$path, [System.Collections.Generic.List[string]]$found) {
    if ($null -eq $node) { $found.Add($path); return }
    if ($node -is [string]) {
        if ($node.Trim().Length -eq 0) { $found.Add($path) }
        return
    }
    if ($node -is [valuetype]) { return }
    if ($node -is [System.Collections.IEnumerable]) {
        $index = 0
        foreach ($item in $node) { Find-EmptyLeaf $item "$path[$index]" $found; $index++ }
        return
    }
    if ($node -is [System.Management.Automation.PSCustomObject]) {
        foreach ($property in $node.PSObject.Properties) {
            Find-EmptyLeaf $property.Value "$path.$($property.Name)" $found
        }
    }
}

# --------------------------------------------------------------------------
# Static surface checks
# --------------------------------------------------------------------------
if (-not (Test-Path $modulePath)) {
    Fail "VcfCredentialRotation.psm1 is missing from the workspace root"
} else {
    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        $modulePath, [ref]$tokens, [ref]$parseErrors)
    Check ($parseErrors.Count -eq 0) (
        'VcfCredentialRotation.psm1 must parse cleanly: ' +
        (($parseErrors | ForEach-Object { $_.Message }) -join '; '))

    $commands = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst]
    }, $true))
    $imports = @($commands | Where-Object {
        $_.GetCommandName() -eq 'Import-Module' -and
        $_.CommandElements.Count -ge 2 -and
        $_.CommandElements[1] -is
            [System.Management.Automation.Language.StringConstantExpressionAst] -and
        $_.CommandElements[1].Value -eq 'VMware.Sdk.Vcf.SddcManager'
    })
    $usingImports = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.UsingStatementAst] -and
        $node.UsingStatementKind -eq
            [System.Management.Automation.Language.UsingStatementKind]::Module -and
        $node.Name.Value -eq 'VMware.Sdk.Vcf.SddcManager'
    }, $true))
    $requiredImports = @()
    if ($null -ne $ast.ScriptRequirements) {
        $requiredImports = @($ast.ScriptRequirements.RequiredModules | Where-Object {
            $_.Name -eq 'VMware.Sdk.Vcf.SddcManager'
        })
    }
    Check ($imports.Count + $usingImports.Count + $requiredImports.Count -ge 1) `
        'the module must import the genuine VMware.Sdk.Vcf.SddcManager module'

    $initializers = @('Initialize-VcfTokenCreationSpec',
                      'Initialize-VcfBaseCredential',
                      'Initialize-VcfResourceCredentials',
                      'Initialize-VcfCredentialsUpdateSpec')
    $definedFunctions = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst]
    }, $true) | ForEach-Object { $_.Name })
    foreach ($initializer in $initializers) {
        Check (@($commands | Where-Object {
            (($_.GetCommandName() -split '\\')[-1]) -eq $initializer
        }).Count -ge 1) `
            "the module must build request bodies with the SDK initializer $initializer"
        Check ($initializer -notin $definedFunctions) `
            "the genuine SDK initializer $initializer must not be replaced by a local function"
    }

    $entryPoint = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Invoke-VcfCredentialRotation'
    }, $true) | Select-Object -First 1)
    if ($entryPoint.Count -eq 1) {
        foreach ($expectedDefault in @{ PageSize = '2'; MaxPolls = '30' }.GetEnumerator()) {
            $parameter = @($entryPoint[0].Body.ParamBlock.Parameters | Where-Object {
                $_.Name.VariablePath.UserPath -eq $expectedDefault.Key
            })
            $actualDefault = $null
            $defaultIsConstant = $false
            if ($parameter.Count -eq 1 -and $null -ne $parameter[0].DefaultValue) {
                try {
                    $actualDefault = $parameter[0].DefaultValue.SafeGetValue()
                    $defaultIsConstant = $true
                } catch { }
            }
            Check ($defaultIsConstant -and
                   [int]$actualDefault -eq [int]$expectedDefault.Value) (
                "Invoke-VcfCredentialRotation -$($expectedDefault.Key) must default to " +
                $expectedDefault.Value)
        }
    }
}

$vendored = @(Get-ChildItem -Path $root -Recurse -Force -File -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -like 'VMware.*' -and
        $_.Extension -in @('.dll', '.psd1', '.psm1') -and
        $_.FullName -notlike "*$([IO.Path]::DirectorySeparatorChar).sandbox-home$([IO.Path]::DirectorySeparatorChar)*"
    })
$vendoredNames = @($vendored | ForEach-Object { $_.Name }) -join ', '
Check ($vendored.Count -eq 0) `
    "VMware SDK modules must not be vendored into the workspace (found: $vendoredNames)"

# --------------------------------------------------------------------------
# Drive the candidate module against the fixture
# --------------------------------------------------------------------------
$logPath = Join-Path ([System.IO.Path]::GetTempPath()) "vcf90-request-log-$PID.json"
$portPath = Join-Path ([System.IO.Path]::GetTempPath()) "vcf90-port-$PID"
Remove-Item $logPath, $portPath -Force -ErrorAction SilentlyContinue

$mock = $null
$result = $null
$invocationError = $null
$script:sleepDelays = [System.Collections.Generic.List[object]]::new()
$script:initializerHits = [ordered]@{
    TokenCreationSpec     = 0
    BaseCredential        = 0
    ResourceCredentials   = 0
    CredentialsUpdateSpec = 0
}
$script:startSleepHits = 0
try {
    $mock = Start-Process -FilePath 'python3' -PassThru -NoNewWindow -ArgumentList @(
        (Join-Path $root 'mock_sddc.py'), $logPath, $portPath)

    $deadline = [datetime]::UtcNow.AddSeconds(30)
    while (-not (Test-Path $portPath) -and [datetime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 50
    }
    if (-not (Test-Path $portPath)) { throw 'the loopback fixture did not start' }
    Start-Sleep -Milliseconds 250
    $port = [int](Get-Content $portPath -Raw).Trim()

    if (Test-Path $modulePath) {
        $candidateModule = Import-Module $modulePath -Force -PassThru -ErrorAction Stop
        $exportedNames = @($candidateModule.ExportedCommands.Keys | Sort-Object)
        Check (($exportedNames -join ',') -eq 'Invoke-VcfCredentialRotation') (
            'the module must export only Invoke-VcfCredentialRotation; exported: ' +
            ($exportedNames -join ', '))
        $command = Get-Command -Name 'Invoke-VcfCredentialRotation' -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            Fail 'the module must export Invoke-VcfCredentialRotation'
        } else {
            foreach ($initializer in $initializers) {
                $resolvedInitializer = Get-Command -Name $initializer -ErrorAction SilentlyContinue
                Check ($null -ne $resolvedInitializer -and
                       $resolvedInitializer.ModuleName -eq 'VMware.Sdk.Vcf.SddcManager') (
                    "$initializer must resolve to the genuine VMware.Sdk.Vcf.SddcManager command")
            }
            $credential = [pscredential]::new(
                'administrator@vsphere.local',
                (ConvertTo-SecureString 'VMw@re1!VMw@re1!' -AsPlainText -Force))
            $initializerBreakpoints = @(
                Set-PSBreakpoint -Command Initialize-VcfTokenCreationSpec -Action {
                    $script:initializerHits.TokenCreationSpec++
                }
                Set-PSBreakpoint -Command Initialize-VcfBaseCredential -Action {
                    $script:initializerHits.BaseCredential++
                }
                Set-PSBreakpoint -Command Initialize-VcfResourceCredentials -Action {
                    $script:initializerHits.ResourceCredentials++
                }
                Set-PSBreakpoint -Command Initialize-VcfCredentialsUpdateSpec -Action {
                    $script:initializerHits.CredentialsUpdateSpec++
                }
                Set-PSBreakpoint -Command Start-Sleep -Action {
                    $script:startSleepHits++
                }
            )
            try {
                $result = Invoke-VcfCredentialRotation `
                    -Server "http://127.0.0.1:$port" `
                    -Credential $credential `
                    -ResourceType 'ESXI' `
                    -CredentialType 'SSH' `
                    -PageSize 2 `
                    -MaxPolls 10 `
                    -SleepAction {
                        param($seconds)
                        $script:sleepDelays.Add($seconds) | Out-Null
                    }
            } catch {
                $invocationError = $_
                Fail "Invoke-VcfCredentialRotation threw: $($_.Exception.Message)"
            } finally {
                Remove-PSBreakpoint -Breakpoint $initializerBreakpoints -ErrorAction SilentlyContinue
            }
        }
    }
} catch {
    Fail "harness error: $($_.Exception.Message)"
} finally {
    if ($null -ne $mock) {
        Stop-Process -Id $mock.Id -Force -ErrorAction SilentlyContinue
        Wait-Process -Id $mock.Id -Timeout 10 -ErrorAction SilentlyContinue
    }
}

Check ($script:sleepDelays.Count -eq 4) (
    'SleepAction must be invoked once between each non-terminal and following poll; ' +
    "expected 4 invocations in the successful run, saw $($script:sleepDelays.Count)")
foreach ($delay in $script:sleepDelays) {
    $numericDelay = 0.0
    Check ([double]::TryParse([string]$delay, [ref]$numericDelay)) (
        "SleepAction must receive its delay in seconds as a number, saw '$delay'")
}
Check ($script:startSleepHits -eq 0) (
    'Start-Sleep must not be called when SleepAction is supplied; ' +
    "saw $script:startSleepHits call(s)")
$expectedInitializerHits = [ordered]@{
    TokenCreationSpec     = 1
    BaseCredential        = 4
    ResourceCredentials   = 4
    CredentialsUpdateSpec = 4
}
foreach ($hit in $expectedInitializerHits.GetEnumerator()) {
    Check ($script:initializerHits[$hit.Key] -eq $hit.Value) (
        "the successful run must genuinely invoke the SDK $($hit.Key) initializer " +
        "$($hit.Value) time(s), saw $($script:initializerHits[$hit.Key])")
}

# --------------------------------------------------------------------------
# Grade the recorded wire traffic
# --------------------------------------------------------------------------
$entries = @()
if (Test-Path $logPath) {
    $raw = (Get-Content $logPath -Raw).Trim()
    if ($raw) { $entries = @($raw | ConvertFrom-Json) }
}
Remove-Item $logPath, $portPath -Force -ErrorAction SilentlyContinue

if ($entries.Count -eq 0) {
    Fail 'the fixture recorded no requests'
} else {
    # -- only contract operations are addressed --------------------------
    $offContract = @($entries | Where-Object { $_.offContract })
    Check ($offContract.Count -eq 0) (
        'requests were sent to targets the pinned 9.0.0.0 contract does not serve: ' +
        (($offContract | ForEach-Object { "$($_.method) $($_.path)" }) -join ', '))

    # -- no empty or null values anywhere on the wire ---------------------
    foreach ($entry in $entries) {
        if ($null -ne $entry.body) {
            $found = [System.Collections.Generic.List[string]]::new()
            Find-EmptyLeaf $entry.body 'body' $found
            Check ($found.Count -eq 0) (
                "$($entry.operationId): unset optional fields must be omitted, " +
                "but these were sent null or empty: $($found -join ', ')")
        }
        foreach ($property in $entry.query.PSObject.Properties) {
            Check ($property.Value.Trim().Length -gt 0) (
                "$($entry.operationId): query parameter '$($property.Name)' was sent empty; " +
                'omit it instead')
        }
    }

    # -- createToken -------------------------------------------------------
    $logins = @($entries | Where-Object { $_.operationId -eq 'createToken' })
    Check ($logins.Count -eq 1) (
        "expected exactly one createToken call, saw $($logins.Count); the expired " +
        'access token must be refreshed, not re-established by logging in again')
    foreach ($login in $logins) {
        Check ($login.method -eq 'POST') "createToken must use POST, saw $($login.method)"
        Check ($login.path -eq '/v1/tokens') "createToken must target /v1/tokens, saw $($login.path)"
        $keys = Get-Keys $login.body
        Check (($keys -join ',') -eq 'password,username') (
            "createToken body must carry exactly username and password (TokenCreationSpec " +
            "apiKey and idToken are unset and must be absent), saw: $($keys -join ', ')")
        Check ($null -eq $login.bearer) 'createToken must not present an Authorization header'
    }

    # -- refreshAccessToken ------------------------------------------------
    $refreshes = @($entries | Where-Object { $_.operationId -eq 'refreshAccessToken' })
    Check ($refreshes.Count -eq 1) (
        "expected exactly one refreshAccessToken call, saw $($refreshes.Count)")
    foreach ($refresh in $refreshes) {
        Check ($refresh.method -eq 'PATCH') (
            "refreshAccessToken is a PATCH in the pinned contract, saw $($refresh.method)")
        Check ($refresh.path -eq '/v1/tokens/access-token/refresh') (
            "refreshAccessToken must target /v1/tokens/access-token/refresh, saw $($refresh.path)")
        Check ($refresh.body -is [string]) (
            'the refreshAccessToken body must be a bare JSON string holding the refresh ' +
            'token id, not an object')
        if ($refresh.body -is [string]) {
            Check ($refresh.body -eq $expectedRefreshTokenId) (
                "refreshAccessToken must send the refresh token id returned by createToken; " +
                "expected '$expectedRefreshTokenId', saw '$($refresh.body)'")
        }
        $expectedRaw = ConvertTo-Json $expectedRefreshTokenId -Compress
        Check ($refresh.rawBody.Trim() -eq $expectedRaw) (
            "refreshAccessToken body must be exactly $expectedRaw on the wire (it must " +
            "not be double-encoded), saw $($refresh.rawBody.Trim())")
        Check ($refresh.status -eq 200) "refreshAccessToken returned $($refresh.status)"
    }

    # -- the expiry was genuinely exercised, then respected -----------------
    $unauthorized = @($entries | Where-Object { $_.status -eq 401 })
    Check ($unauthorized.Count -eq 1) (
        'the fixture expires one access token and each 401 must trigger an immediate refresh; ' +
        "saw $($unauthorized.Count) rejected requests")

    $refreshIndex = -1
    $rejectedIndex = -1
    for ($i = 0; $i -lt $entries.Count; $i++) {
        if ($rejectedIndex -lt 0 -and $entries[$i].status -eq 401) { $rejectedIndex = $i }
        if ($entries[$i].operationId -eq 'refreshAccessToken') { $refreshIndex = $i; break }
    }
    Check ($rejectedIndex -ge 0 -and $refreshIndex -eq $rejectedIndex + 1) (
        'refreshAccessToken must immediately follow the rejected request')
    if ($rejectedIndex -ge 0 -and $refreshIndex -eq $rejectedIndex + 1 -and
        $refreshIndex + 1 -lt $entries.Count) {
        $rejected = $entries[$rejectedIndex]
        $replay = $entries[$refreshIndex + 1]
        Check ($replay.operationId -eq $rejected.operationId -and
               $replay.method -eq $rejected.method -and
               $replay.path -eq $rejected.path -and
               $replay.rawQuery -eq $rejected.rawQuery -and
               $replay.rawBody -eq $rejected.rawBody) (
            'the request immediately following refreshAccessToken must be an exact replay ' +
            'of the rejected request')
    } elseif ($refreshIndex -ge 0) {
        Fail 'refreshAccessToken was not followed by a replay of the rejected request'
    }
    if ($refreshIndex -ge 0) {
        for ($i = $refreshIndex + 1; $i -lt $entries.Count; $i++) {
            $entry = $entries[$i]
            if ($null -ne $entry.bearer) {
                Check ($entry.bearer -eq $refreshedAccessToken) (
                    "$($entry.operationId) was sent with the expired access token after the " +
                    'refresh; every later call must use the refreshed token')
            }
            Check ($entry.status -ne 401) (
                "$($entry.operationId) was still rejected with 401 after the refresh")
        }
        $before = @($entries[0..$refreshIndex] | Where-Object { $null -ne $_.bearer })
        foreach ($entry in $before) {
            Check ($entry.bearer -eq $initialAccessToken) (
                "$($entry.operationId) presented an unexpected token before the refresh")
        }
    }

    # -- getCredentials ----------------------------------------------------
    $listings = @($entries | Where-Object { $_.operationId -eq 'getCredentials' })
    Check ($listings.Count -ge 1) 'the module never called getCredentials'
    foreach ($listing in $listings) {
        Check ($listing.method -eq 'GET') "getCredentials must use GET, saw $($listing.method)"
        $queryKeys = Get-Keys $listing.query
        $unexpected = @($queryKeys | Where-Object { $_ -notin $allowedQueryKeys })
        Check ($unexpected.Count -eq 0) (
            "getCredentials sent query parameters that were never set: $($unexpected -join ', '); " +
            'unset optional parameters must be omitted from the query string')
        $leaked = @($queryKeys | Where-Object { $_ -in $forbiddenQueryKeys })
        Check ($leaked.Count -eq 0) (
            "getCredentials must omit the unset filters $($leaked -join ', ')")
        Check (Test-HasKey $listing.query 'resourceType') 'getCredentials must filter by resourceType'
        if (Test-HasKey $listing.query 'resourceType') {
            Check ($listing.query.resourceType -eq 'ESXI') (
                "getCredentials must request resourceType ESXI, saw $($listing.query.resourceType)")
        }
        Check ($null -ne $listing.bearer) 'getCredentials must present a bearer token'
    }
    $succeeded = @($listings | Where-Object { $_.status -eq 200 })
    $pages = @($succeeded | Where-Object { Test-HasKey $_.query 'pageNumber' } |
        ForEach-Object { [int]$_.query.pageNumber } | Sort-Object -Unique)
    Check (($pages -join ',') -eq '0,1,2,3,4') (
        "the module must page through every page of results; pages fetched: $($pages -join ', ')")
    foreach ($listing in $succeeded) {
        if (Test-HasKey $listing.query 'pageSize') {
            Check ($listing.query.pageSize -eq '2') (
                "getCredentials must honour the requested page size of 2, saw $($listing.query.pageSize)")
        }
    }

    # -- updateOrRotatePasswords ------------------------------------------
    $rotations = @($entries | Where-Object { $_.operationId -eq 'updateOrRotatePasswords' })
    $accepted = @($rotations | Where-Object { $_.status -eq 202 })
    Check ($rotations.Count -ge 1) 'the module never called updateOrRotatePasswords'

    $rotatedIds = [System.Collections.Generic.List[string]]::new()
    foreach ($rotation in $accepted) {
        Check ($rotation.method -eq 'PATCH') (
            "updateOrRotatePasswords is a PATCH in the pinned contract, saw $($rotation.method)")
        Check ($rotation.path -eq '/v1/credentials') (
            "updateOrRotatePasswords must target /v1/credentials, saw $($rotation.path)")
        $bodyKeys = Get-Keys $rotation.body
        Check (($bodyKeys -join ',') -eq 'elements,operationType') (
            'the CredentialsUpdateSpec must carry exactly operationType and elements ' +
            "(autoRotatePolicy is unset and must be absent), saw: $($bodyKeys -join ', ')")
        Check ($rotation.body.operationType -eq 'ROTATE') (
            "operationType must be ROTATE, saw $($rotation.body.operationType)")

        foreach ($element in @($rotation.body.elements)) {
            $elementKeys = Get-Keys $element
            $unknown = @($elementKeys | Where-Object {
                $_ -notin @('resourceName', 'resourceId', 'resourceType', 'credentials') })
            Check ($unknown.Count -eq 0) (
                "ResourceCredentials carried members outside the contract: $($unknown -join ', ')")
            Check (Test-HasKey $element 'resourceType') 'ResourceCredentials.resourceType is required'
            Check (Test-HasKey $element 'credentials') 'ResourceCredentials.credentials is required'
            Check (Test-HasKey $element 'resourceId') (
                'ResourceCredentials must identify the host with resourceId')
            if (Test-HasKey $element 'resourceId') { $rotatedIds.Add($element.resourceId) | Out-Null }
            if (Test-HasKey $element 'resourceType') {
                Check ($element.resourceType -eq 'ESXI') (
                    "ResourceCredentials must preserve resourceType ESXI, saw $($element.resourceType)")
            }
            if (Test-HasKey $element 'credentials') {
                Check (@($element.credentials).Count -eq 1) (
                    'each fixture host has exactly one matching SSH BaseCredential; ' +
                    "the rotation carried $(@($element.credentials).Count)")
            }

            foreach ($credential in @($element.credentials)) {
                $credentialKeys = Get-Keys $credential
                $unknown = @($credentialKeys | Where-Object {
                    $_ -notin @('credentialType', 'accountType', 'username', 'password') })
                Check ($unknown.Count -eq 0) (
                    "BaseCredential carried members outside the contract: $($unknown -join ', ')")
                Check (-not (Test-HasKey $credential 'password')) (
                    'a ROTATE request must omit BaseCredential.password so SDDC Manager ' +
                    'generates the new secret')
                Check (Test-HasKey $credential 'username') 'BaseCredential.username is required'
                if (Test-HasKey $credential 'credentialType') {
                    Check ($credential.credentialType -eq 'SSH') (
                        "only SSH credentials were requested, saw credentialType " +
                        "$($credential.credentialType)")
                }
                if (Test-HasKey $credential 'username') {
                    Check ($credential.username -eq 'root') (
                        "expected the ESXi SSH account 'root', saw $($credential.username)")
                }
            }
        }
    }

    $sortedRotated = @($rotatedIds | Sort-Object)
    Check (($sortedRotated -join ',') -eq ($expectedRotatedIds -join ',')) (
        'each host holding an SSH credential must be rotated exactly once and no other ' +
        "host may be touched; accepted rotations were: $($sortedRotated -join ', ')")

    # -- getCredentialsTask -------------------------------------------------
    $submittedTasks = @($entries | Where-Object {
        $_.operationId -eq 'updateOrRotatePasswords' -and $_.status -eq 202 })
    $polls = @($entries | Where-Object { $_.operationId -eq 'getCredentialsTask' })
    Check ($polls.Count -ge $submittedTasks.Count) (
        'every accepted rotation task must be polled to a terminal state')
    foreach ($poll in $polls) {
        Check ($poll.method -eq 'GET') "getCredentialsTask must use GET, saw $($poll.method)"
        Check ($poll.path -match '^/v1/credentials/tasks/[^/]+$') (
            "getCredentialsTask must target /v1/credentials/tasks/{id}, saw $($poll.path)")
        Check ($null -ne $poll.bearer) 'getCredentialsTask must present a bearer token'
    }
    $polledTaskIds = @($polls | Where-Object { $_.status -eq 200 } |
        ForEach-Object { $_.path -replace '^/v1/credentials/tasks/', '' } | Sort-Object -Unique)
    Check ($polledTaskIds.Count -eq $expectedRotatedIds.Count) (
        "expected $($expectedRotatedIds.Count) rotation tasks to be polled, " +
        "saw $($polledTaskIds.Count): $($polledTaskIds -join ', ')")
}

# --------------------------------------------------------------------------
# Grade the returned summary
# --------------------------------------------------------------------------
if ($null -eq $result) {
    if ($null -eq $invocationError) { Fail 'Invoke-VcfCredentialRotation returned nothing' }
} else {
    foreach ($property in @('ResourceIds', 'TaskIds', 'TokenRefreshed')) {
        Check (Test-HasKey $result $property) (
            "the returned summary must expose a $property property")
    }
    if (Test-HasKey $result 'ResourceIds') {
        $reported = @($result.ResourceIds | Sort-Object)
        Check (($reported -join ',') -eq ($expectedRotatedIds -join ',')) (
            "the summary must report the rotated hosts, saw: $($reported -join ', ')")
    }
    if (Test-HasKey $result 'TaskIds') {
        Check (@($result.TaskIds).Count -eq $expectedRotatedIds.Count) (
            "the summary must report one task id per rotation, saw $(@($result.TaskIds).Count)")
    }
    if (Test-HasKey $result 'TokenRefreshed') {
        Check ([bool]$result.TokenRefreshed) (
            'the summary must report that the access token was refreshed during the run')
    }
}

# --------------------------------------------------------------------------
# Focused polling behavior checks
# --------------------------------------------------------------------------
function Invoke-PollingScenario(
    [string]$mode,
    [int]$maxPolls,
    [string]$credentialType = 'SSH'
) {
    $suffix = $mode.Replace(':', '-').Replace('_', '-').ToLowerInvariant()
    $scenarioLogPath = Join-Path ([System.IO.Path]::GetTempPath()) (
        "vcf90-$suffix-request-log-$PID.json")
    $scenarioPortPath = Join-Path ([System.IO.Path]::GetTempPath()) (
        "vcf90-$suffix-port-$PID")
    Remove-Item $scenarioLogPath, $scenarioPortPath -Force -ErrorAction SilentlyContinue

    $scenarioMock = $null
    $scenarioError = $null
    $scenarioHarnessError = $null
    $script:scenarioSleepDelays = [System.Collections.Generic.List[object]]::new()
    try {
        $scenarioMock = Start-Process -FilePath 'python3' -PassThru -NoNewWindow -ArgumentList @(
            (Join-Path $root 'mock_sddc.py'), $scenarioLogPath, $scenarioPortPath, $mode)
        $scenarioDeadline = [datetime]::UtcNow.AddSeconds(30)
        while (-not (Test-Path $scenarioPortPath) -and
               [datetime]::UtcNow -lt $scenarioDeadline) {
            Start-Sleep -Milliseconds 50
        }
        if (-not (Test-Path $scenarioPortPath)) {
            throw "the loopback fixture did not start for polling mode '$mode'"
        }
        $scenarioPort = [int](Get-Content $scenarioPortPath -Raw).Trim()
        $scenarioCredential = [pscredential]::new(
            'administrator@vsphere.local',
            (ConvertTo-SecureString 'VMw@re1!VMw@re1!' -AsPlainText -Force))
        try {
            $null = Invoke-VcfCredentialRotation `
                -Server "http://127.0.0.1:$scenarioPort" `
                -Credential $scenarioCredential `
                -ResourceType 'ESXI' `
                -CredentialType $credentialType `
                -PageSize 50 `
                -MaxPolls $maxPolls `
                -SleepAction {
                    param($seconds)
                    $script:scenarioSleepDelays.Add($seconds) | Out-Null
                }
        } catch {
            $scenarioError = $_
        }
    } catch {
        $scenarioHarnessError = $_
    } finally {
        if ($null -ne $scenarioMock) {
            Stop-Process -Id $scenarioMock.Id -Force -ErrorAction SilentlyContinue
            Wait-Process -Id $scenarioMock.Id -Timeout 10 -ErrorAction SilentlyContinue
        }
    }

    $scenarioEntries = @()
    if (Test-Path $scenarioLogPath) {
        $scenarioRaw = (Get-Content $scenarioLogPath -Raw).Trim()
        if ($scenarioRaw) { $scenarioEntries = @($scenarioRaw | ConvertFrom-Json) }
    }
    Remove-Item $scenarioLogPath, $scenarioPortPath -Force -ErrorAction SilentlyContinue
    return [pscustomobject]@{
        Error        = $scenarioError
        HarnessError = $scenarioHarnessError
        Entries      = $scenarioEntries
        SleepDelays  = @($script:scenarioSleepDelays)
    }
}

if ($null -ne (Get-Command -Name 'Invoke-VcfCredentialRotation' -ErrorAction SilentlyContinue)) {
    foreach ($terminalStatus in @('FAILED', 'USER_CANCELLED', 'INCONSISTENT')) {
        $scenarioCredentialType = if ($terminalStatus -eq 'FAILED') { 'API' } else { 'SSH' }
        $observation = Invoke-PollingScenario `
            -Mode "terminal:$terminalStatus" -MaxPolls 3 `
            -CredentialType $scenarioCredentialType
        Check ($null -eq $observation.HarnessError) (
            "polling scenario $terminalStatus failed to run: " +
            [string]$observation.HarnessError)
        Check ($null -ne $observation.Error) (
            "a credential task in terminal state $terminalStatus must fail the rotation")
        $completedPolls = @($observation.Entries | Where-Object {
            $_.operationId -eq 'getCredentialsTask' -and $_.status -eq 200
        })
        Check ($completedPolls.Count -eq 1) (
            "terminal state $terminalStatus must stop polling immediately; " +
            "successful polls: $($completedPolls.Count)")
        Check ($observation.SleepDelays.Count -eq 0) (
            "SleepAction must not run after terminal state $terminalStatus")
        $scenarioRotations = @($observation.Entries | Where-Object {
            $_.operationId -eq 'updateOrRotatePasswords' -and $_.status -eq 202
        })
        Check ($scenarioRotations.Count -eq 1) (
            "terminal state $terminalStatus scenario must submit exactly one rotation before failing")
        foreach ($scenarioRotation in $scenarioRotations) {
            foreach ($element in @($scenarioRotation.body.elements)) {
                foreach ($baseCredential in @($element.credentials)) {
                    $expectedScenarioUsername = if ($scenarioCredentialType -eq 'API') {
                        'svc-vcf-01'
                    } else { 'root' }
                    Check ((Test-HasKey $baseCredential 'username') -and
                           $baseCredential.username -eq $expectedScenarioUsername) (
                        "CredentialType $scenarioCredentialType was requested, but the " +
                        "rotation did not carry its listed username $expectedScenarioUsername")
                    if (Test-HasKey $baseCredential 'credentialType') {
                        Check ($baseCredential.credentialType -eq $scenarioCredentialType) (
                            "CredentialType $scenarioCredentialType was requested, but the " +
                            "rotation sent $($baseCredential.credentialType)")
                    }
                }
            }
        }
    }

    $limitObservation = Invoke-PollingScenario -Mode 'never-terminal' -MaxPolls 2
    Check ($null -eq $limitObservation.HarnessError) (
        'poll-limit scenario failed to run: ' +
        [string]$limitObservation.HarnessError)
    Check ($null -ne $limitObservation.Error) (
        'a credential task that remains non-terminal through MaxPolls must fail the rotation')
    $limitPolls = @($limitObservation.Entries | Where-Object {
        $_.operationId -eq 'getCredentialsTask' -and $_.status -eq 200
    })
    Check ($limitPolls.Count -eq 2) (
        "MaxPolls 2 must permit exactly two successful polls, saw $($limitPolls.Count)")
    Check ($limitObservation.SleepDelays.Count -eq 1) (
        'SleepAction must run between permitted polls but never after the final permitted poll; ' +
        "expected 1 invocation, saw $($limitObservation.SleepDelays.Count)")
}

# --------------------------------------------------------------------------
if ($script:failures.Count -gt 0) {
    Write-Host "FAILED ($($script:failures.Count) problem(s)):" -ForegroundColor Red
    foreach ($failure in $script:failures) { Write-Host "  - $failure" }
    exit 1
}
Write-Host 'PASSED: credential rotation survived the access token expiry without losing work.'
exit 0
