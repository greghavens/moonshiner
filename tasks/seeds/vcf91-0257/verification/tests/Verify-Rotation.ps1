<#
.SYNOPSIS
    Protected verifier for Invoke-VcfOpsCredentialRotation.

.DESCRIPTION
    Starts the contract-pinned loopback mock, runs the rotation once, then
    asserts the exact wire shape of every request the module emitted.

    Deterministic and offline: no live VMware endpoint is contacted.

    Exit code 0 = pass, 1 = fail.
#>
[CmdletBinding()]
param(
    [string] $Root = (Split-Path -Parent (Split-Path -Parent $PSCommandPath))
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$MockScript   = Join-Path $Root 'tools/Start-VcfOpsMock.ps1'
$ContractPath = Join-Path $Root 'docs/contract.json'
$StatePath    = Join-Path $Root 'tools/mock-state.json'
$ModulePath   = Join-Path $Root 'src/VcfOpsCredentialRotation/VcfOpsCredentialRotation.psd1'

$OLD_ID   = '0f8b0e42-1a2b-4c3d-8e9f-a1b2c3d4e5f6'
$NEW_ID   = '7c1d9a55-2b3c-4d5e-9f0a-b1c2d3e4f5a6'
$DR_CRED  = '3d5e7f91-4a6b-4c8d-9e0f-1a2b3c4d5e6f'
$DR_ADPT  = '44444444-aaaa-4000-8000-000000000004'
$FLAKY    = '22222222-aaaa-4000-8000-000000000002'
$BOUND    = @(
    '11111111-aaaa-4000-8000-000000000001',
    '22222222-aaaa-4000-8000-000000000002',
    '33333333-aaaa-4000-8000-000000000003'
)

$CRED_NAME   = 'vc-collector-svc'
$NEW_NAME    = 'vc-collector-svc-2026q3'
$NEW_SECRET  = 'Rot@ted-Secret-9f2c'
$FIELD_NAME  = 'password'
$ADAPTER_KIND = 'VMWARE'
$TOKEN       = 'OpsToken ops-token-2f1c4b'

$script:failures = [System.Collections.Generic.List[string]]::new()
$script:checks   = 0

function Test-Claim([bool]$Condition, [string]$Message) {
    $script:checks++
    if (-not $Condition) { $script:failures.Add($Message) }
}

# Walk parsed JSON and report every property that is null or an empty string.
function Get-EmptyPath($Node, [string]$Prefix) {
    $bad = @()
    if ($null -eq $Node) { return @($Prefix) }
    if ($Node -is [System.Management.Automation.PSCustomObject]) {
        foreach ($p in $Node.PSObject.Properties) {
            $path = if ($Prefix) { "$Prefix.$($p.Name)" } else { $p.Name }
            $v = $p.Value
            if ($null -eq $v) { $bad += $path }
            elseif (($v -is [string]) -and $v -eq '') { $bad += $path }
            elseif (($v -is [array]) -or ($v -is [System.Management.Automation.PSCustomObject])) {
                $bad += Get-EmptyPath $v $path
            }
        }
    }
    elseif ($Node -is [array]) {
        for ($i = 0; $i -lt $Node.Count; $i++) {
            $bad += Get-EmptyPath $Node[$i] "$Prefix[$i]"
        }
    }
    return $bad
}

function Get-PropertyName($Obj) {
    return @($Obj.PSObject.Properties.Name | Sort-Object)
}

# --- pick a free loopback port -------------------------------------------
$probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$probe.Start()
$port = ([System.Net.IPEndPoint]$probe.LocalEndpoint).Port
$probe.Stop()

$work    = Join-Path ([System.IO.Path]::GetTempPath()) ("vcfops-verify-" + [guid]::NewGuid().ToString('n'))
New-Item -ItemType Directory -Path $work | Out-Null
$logPath   = Join-Path $work 'requests.jsonl'
$readyPath = Join-Path $work 'ready'

$mock = Start-Process -FilePath (Get-Process -Id $PID).Path -PassThru `
    -ArgumentList @(
        '-NoProfile', '-File', $MockScript,
        '-Port', $port,
        '-LogPath', $logPath,
        '-ContractPath', $ContractPath,
        '-StatePath', $StatePath,
        '-ReadyPath', $readyPath
    )

$result = $null
$invokeError = $null
try {
    $deadline = [datetime]::UtcNow.AddSeconds(60)
    while (-not (Test-Path -LiteralPath $readyPath)) {
        if ([datetime]::UtcNow -gt $deadline) { throw "mock did not become ready on port $port" }
        Start-Sleep -Milliseconds 100
    }

    Import-Module $ModulePath -Force -ErrorAction Stop

    try {
        $result = Invoke-VcfOpsCredentialRotation `
            -Server '127.0.0.1' -Port $port -Protocol 'http' `
            -User 'svc-rotation' -Password 'bootstrap-pw' `
            -AdapterKind $ADAPTER_KIND `
            -CredentialName $CRED_NAME `
            -NewCredentialName $NEW_NAME `
            -NewSecret $NEW_SECRET `
            -SecretFieldName $FIELD_NAME
    }
    catch {
        $invokeError = $_
    }
}
finally {
    Start-Sleep -Milliseconds 300
    if ($mock -and -not $mock.HasExited) { $mock.Kill() }
}

Test-Claim ($null -eq $invokeError) "Invoke-VcfOpsCredentialRotation threw: $invokeError"

# --- load the request log ------------------------------------------------
$entries = @()
if (Test-Path -LiteralPath $logPath) {
    $entries = @(Get-Content -LiteralPath $logPath |
        Where-Object { $_.Trim() } |
        ForEach-Object { $_ | ConvertFrom-Json })
}
Test-Claim ($entries.Count -gt 0) 'the mock recorded no requests at all'

function Select-Op([string]$Name) {
    return @($entries | Where-Object { $_.operationId -eq $Name })
}

$acquire  = @(Select-Op 'acquireToken')
$getCreds = @(Select-Op 'getCredentials')
$create   = @(Select-Op 'createCredential')
$enum     = @(Select-Op 'enumerateAdapterInstances')
$patches  = @(Select-Op 'patchAdapterInstance')
$retire   = @(Select-Op 'partialUpdateCredential')
$release  = @(Select-Op 'releaseToken')

# --- 1. returned summary -------------------------------------------------
if ($result) {
    $expectedResultProperties = @(
        'OldCredentialId',
        'NewCredentialId',
        'RepointedAdapterIds',
        'Drained',
        'Retired',
        'RetiredCredentialName'
    )
    $actualResultProperties = @($result.PSObject.Properties.Name)
    Test-Claim (($actualResultProperties -join ',') -eq ($expectedResultProperties -join ',')) `
        ("result properties were '$($actualResultProperties -join ',')', expected exactly " +
         "'$($expectedResultProperties -join ',')'")
    Test-Claim ([string]$result.OldCredentialId -eq $OLD_ID) `
        "OldCredentialId was '$($result.OldCredentialId)', expected $OLD_ID"
    Test-Claim ([string]$result.NewCredentialId -eq $NEW_ID) `
        "NewCredentialId was '$($result.NewCredentialId)', expected $NEW_ID"
    Test-Claim ([bool]$result.Drained) 'Drained was not $true'
    Test-Claim ([bool]$result.Retired) 'Retired was not $true'
    Test-Claim ([string]$result.RetiredCredentialName -eq "$CRED_NAME (retired)") `
        "RetiredCredentialName was '$($result.RetiredCredentialName)'"
    $repointed = @($result.RepointedAdapterIds | ForEach-Object { [string]$_ } | Sort-Object)
    Test-Claim (($repointed -join ',') -eq ($BOUND -join ',')) `
        "RepointedAdapterIds was '$($repointed -join ',')', expected '$($BOUND -join ',')'"
}
else {
    Test-Claim $false 'the rotation returned nothing'
}

# --- 2. contract discipline ---------------------------------------------
$offContract = @($entries | Where-Object { $_.outOfContract })
Test-Claim ($offContract.Count -eq 0) `
    ("requests outside the contract: " + (($offContract | ForEach-Object { "$($_.method) $($_.path)" }) -join '; '))

$violations = @($entries | Where-Object { $_.PSObject.Properties.Name -contains 'violation' })
Test-Claim ($violations.Count -eq 0) `
    ("the mock recorded stranding violations: " + (($violations | ForEach-Object { $_.violation }) -join '; '))

foreach ($e in $entries) {
    $ua = [string]$e.userAgent
    Test-Claim ($ua -eq 'PowerCLI' -or $ua.StartsWith('VMware.Sdk.Vcf.Ops/')) `
        "seq $($e.seq) ($($e.operationId)) used User-Agent '$ua'; requests must come from the VMware.Sdk.Vcf.Ops cmdlets"
}

foreach ($e in $entries | Where-Object { $_.operationId -ne 'acquireToken' }) {
    Test-Claim ([string]$e.authorization -eq $TOKEN) `
        "seq $($e.seq) ($($e.operationId)) sent Authorization '$($e.authorization)', expected '$TOKEN'"
}

# Only the two injected transient failures may be non-success statuses. Making
# the busy adapter fail twice also proves the documented default of three
# attempts rather than merely proving that one retry happened.
$bad = @($entries | Where-Object { $_.status -ge 400 })
Test-Claim ($bad.Count -eq 2 -and @($bad | Where-Object { $_.status -eq 503 }).Count -eq 2) `
    ("expected exactly two 503 responses (the injected transient failures), saw: " +
     (($bad | ForEach-Object { "$($_.operationId)=$($_.status)" }) -join ', '))

# --- 3. unset optional fields are omitted, never sent empty --------------
# Scoped to the bodies this module builds. The acquireToken body is assembled
# by Connect-VcfOpsServer itself, which always emits authSource, so it is not
# something an implementation can influence.
$authored = @('createCredential', 'patchAdapterInstance', 'partialUpdateCredential')
foreach ($e in $entries | Where-Object { $authored -contains $_.operationId }) {
    if (-not $e.body) { continue }
    $parsed = $null
    try { $parsed = $e.body | ConvertFrom-Json } catch { }
    if ($null -eq $parsed) { continue }
    $empty = @(Get-EmptyPath $parsed '')
    Test-Claim ($empty.Count -eq 0) `
        ("seq $($e.seq) ($($e.operationId)) sent empty values for: " + ($empty -join ', ') +
         " -- unset optional fields must be omitted from the body")
}

# --- 4. createCredential -------------------------------------------------
Test-Claim ($create.Count -eq 1) "expected exactly 1 createCredential, saw $($create.Count)"
if ($create.Count -eq 1) {
    $b = $create[0].body | ConvertFrom-Json
    $names = Get-PropertyName $b
    Test-Claim (($names -join ',') -eq 'adapterKindKey,credentialKindKey,fields,name') `
        ("createCredential body had properties '$($names -join ',')'; " +
         "expected exactly 'adapterKindKey,credentialKindKey,fields,name' " +
         "(id must be omitted on create, and editable was never set)")
    Test-Claim ([string]$b.adapterKindKey -eq $ADAPTER_KIND) `
        "createCredential adapterKindKey was '$($b.adapterKindKey)'"
    Test-Claim ([string]$b.credentialKindKey -eq 'PRINCIPALCREDENTIAL') `
        "createCredential credentialKindKey was '$($b.credentialKindKey)'; it must be carried from the credential being rotated"
    Test-Claim ([string]$b.name -eq $NEW_NAME) "createCredential name was '$($b.name)'"

    $fields = @($b.fields)
    Test-Claim ($fields.Count -eq 1) "createCredential sent $($fields.Count) field(s), expected 1"
    if ($fields.Count -ge 1) {
        $fn = Get-PropertyName $fields[0]
        Test-Claim (($fn -join ',') -eq 'name,value') `
            "createCredential field had properties '$($fn -join ',')', expected 'name,value'"
        Test-Claim ([string]$fields[0].name -eq $FIELD_NAME) `
            "createCredential field name was '$($fields[0].name)'"
        Test-Claim ([string]$fields[0].value -eq $NEW_SECRET) `
            'createCredential did not carry the new secret'
    }
    Test-Claim ($create[0].status -eq 201) "createCredential returned $($create[0].status)"
}

# --- 5. patchAdapterInstance --------------------------------------------
# three bound adapters, one of which needs all three default attempts
Test-Claim ($patches.Count -eq 5) `
    ("expected 5 patchAdapterInstance calls (3 adapters + 2 retries of the " +
     "transient failures), saw $($patches.Count)")

$patchedIds = @()
foreach ($p in $patches) {
    $b = $p.body | ConvertFrom-Json
    $names = Get-PropertyName $b
    Test-Claim (($names -join ',') -eq 'credentialInstanceId,id,resourceKey') `
        ("patchAdapterInstance (seq $($p.seq)) body had properties '$($names -join ',')'; " +
         "expected exactly 'credentialInstanceId,id,resourceKey'")
    Test-Claim ([string]$b.credentialInstanceId -eq $NEW_ID) `
        "patchAdapterInstance (seq $($p.seq)) pointed at '$($b.credentialInstanceId)', expected the staged credential $NEW_ID"
    if ($b.PSObject.Properties.Name -contains 'resourceKey') {
        $rk = Get-PropertyName $b.resourceKey
        Test-Claim (($rk -join ',') -eq 'adapterKindKey,name,resourceKindKey') `
            ("patchAdapterInstance (seq $($p.seq)) resourceKey had properties '$($rk -join ',')'; " +
             "expected exactly 'adapterKindKey,name,resourceKindKey'")
    }
    $patchedIds += [string]$b.id
}

$distinct = @($patchedIds | Sort-Object -Unique)
Test-Claim (($distinct -join ',') -eq ($BOUND -join ',')) `
    "patched adapter ids were '$($distinct -join ',')', expected '$($BOUND -join ',')'"
Test-Claim ($patchedIds -notcontains $DR_ADPT) `
    "adapter $DR_ADPT belongs to another credential and must not be repointed"
Test-Claim (@($patchedIds | Where-Object { $_ -eq $FLAKY }).Count -eq 3) `
    "the adapter that returned 503 twice must use the default three attempts"

# --- 6. the old credential is retired, never rewritten in place ----------
Test-Claim ($retire.Count -eq 1) "expected exactly 1 partialUpdateCredential, saw $($retire.Count)"
if ($retire.Count -eq 1) {
    $b = $retire[0].body | ConvertFrom-Json
    $names = Get-PropertyName $b
    Test-Claim (($names -join ',') -eq 'adapterKindKey,credentialKindKey,id,name') `
        ("partialUpdateCredential body had properties '$($names -join ',')'; expected exactly " +
         "'adapterKindKey,credentialKindKey,id,name'")
    Test-Claim ($names -contains 'id') 'partialUpdateCredential must carry the credential id'
    Test-Claim ([string]$b.id -eq $OLD_ID) `
        "partialUpdateCredential targeted '$($b.id)', expected the old credential $OLD_ID"
    Test-Claim ($names -notcontains 'fields') `
        'partialUpdateCredential must not rewrite the secret in place; that is what strands in-flight collection'
    Test-Claim ([string]$b.name -eq "$CRED_NAME (retired)") `
        "partialUpdateCredential name was '$($b.name)', expected '$CRED_NAME (retired)'"
    Test-Claim ($retire[0].status -eq 200) "partialUpdateCredential returned $($retire[0].status)"
}

# --- 7. ordering: stage, repoint, prove drained, only then retire --------
if ($create.Count -eq 1 -and $patches.Count -gt 0) {
    $firstPatch = ($patches | Measure-Object -Property seq -Minimum).Minimum
    Test-Claim ($create[0].seq -lt $firstPatch) `
        'the replacement credential must exist before any adapter is repointed'
}
if ($patches.Count -gt 0 -and $retire.Count -eq 1) {
    $lastPatch = ($patches | Measure-Object -Property seq -Maximum).Maximum
    Test-Claim ($lastPatch -lt $retire[0].seq) `
        'every adapter must be repointed before the old credential is touched'

    $drainCheck = @($enum | Where-Object { $_.seq -gt $lastPatch -and $_.seq -lt $retire[0].seq })
    Test-Claim ($drainCheck.Count -ge 1) `
        ('the old credential was retired without re-reading the adapter instances first; ' +
         'the rotation must prove nothing still references it')
}

# --- 8. the new secret never leaves the request body ---------------------
foreach ($e in $entries) {
    $q = ($e.query | ConvertTo-Json -Depth 6 -Compress)
    Test-Claim (-not ([string]$e.path).Contains($NEW_SECRET)) `
        "seq $($e.seq) leaked the new secret in the request path"
    Test-Claim (-not $q.Contains($NEW_SECRET)) `
        "seq $($e.seq) leaked the new secret in the query string"
}
$carriers = @($entries | Where-Object { $_.body -and ([string]$_.body).Contains($NEW_SECRET) })
Test-Claim ($carriers.Count -eq 1 -and $carriers[0].operationId -eq 'createCredential') `
    ('the new secret must appear only in the createCredential body, but appeared in: ' +
     (($carriers | ForEach-Object { $_.operationId }) -join ', '))

# --- 9. session hygiene --------------------------------------------------
Test-Claim ($acquire.Count -ge 1) 'the module never acquired a token'
Test-Claim ($getCreds.Count -ge 1) 'the module never looked up the credential by name'
Test-Claim ($release.Count -eq 1) `
    "expected the session to be released exactly once, saw $($release.Count) releaseToken call(s)"

# --- 10. an adapter left behind prevents retirement ---------------------
# Repeat against fresh appliance state, but keep one collector busy for
# every attempt. Whether the function reports or throws after exhaustion,
# it must bound the retries, leave the old credential untouched, and release
# its session.
$failureStatePath = Join-Path $work 'persistent-failure-state.json'
$failureLogPath   = Join-Path $work 'persistent-failure-requests.jsonl'
$failureReadyPath = Join-Path $work 'persistent-failure-ready'
$failureState = Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json
$failureState | Add-Member -NotePropertyName persistentFailAdapterId -NotePropertyValue $FLAKY
$failureState | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $failureStatePath -Encoding utf8

$failureProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$failureProbe.Start()
$failurePort = ([System.Net.IPEndPoint]$failureProbe.LocalEndpoint).Port
$failureProbe.Stop()

$failureMock = Start-Process -FilePath (Get-Process -Id $PID).Path -PassThru `
    -ArgumentList @(
        '-NoProfile', '-File', $MockScript,
        '-Port', $failurePort,
        '-LogPath', $failureLogPath,
        '-ContractPath', $ContractPath,
        '-StatePath', $failureStatePath,
        '-ReadyPath', $failureReadyPath
    )

$failureResult = $null
try {
    $deadline = [datetime]::UtcNow.AddSeconds(60)
    while (-not (Test-Path -LiteralPath $failureReadyPath)) {
        if ([datetime]::UtcNow -gt $deadline) {
            throw "persistent-failure mock did not become ready on port $failurePort"
        }
        Start-Sleep -Milliseconds 100
    }

    try {
        $failureResult = Invoke-VcfOpsCredentialRotation `
            -Server '127.0.0.1' -Port $failurePort -Protocol 'http' `
            -User 'svc-rotation' -Password 'bootstrap-pw' `
            -AdapterKind $ADAPTER_KIND `
            -CredentialName $CRED_NAME `
            -NewCredentialName "$NEW_NAME-failure" `
            -NewSecret $NEW_SECRET `
            -SecretFieldName $FIELD_NAME `
            -MaxAttempts 2
    }
    catch {
        # Exhaustion may be a terminating failure. The wire assertions below
        # are the safety contract: never retire a credential still in use.
    }
}
finally {
    Start-Sleep -Milliseconds 300
    if ($failureMock -and -not $failureMock.HasExited) { $failureMock.Kill() }
}

$failureEntries = @()
if (Test-Path -LiteralPath $failureLogPath) {
    $failureEntries = @(Get-Content -LiteralPath $failureLogPath |
        Where-Object { $_.Trim() } |
        ForEach-Object { $_ | ConvertFrom-Json })
}
Test-Claim ($failureEntries.Count -gt 0) 'the persistent-failure mock recorded no requests'

$failurePatches = @($failureEntries | Where-Object { $_.operationId -eq 'patchAdapterInstance' })
$failureFlakyPatches = @($failurePatches | Where-Object {
    if (-not $_.body) { return $false }
    try { return ([string](($_.body | ConvertFrom-Json).id) -eq $FLAKY) }
    catch { return $false }
})
Test-Claim ($failureFlakyPatches.Count -eq 2) `
    "MaxAttempts 2 produced $($failureFlakyPatches.Count) attempts for the persistently busy adapter"
Test-Claim (@($failureFlakyPatches | Where-Object { $_.status -ne 503 }).Count -eq 0) `
    'the persistent-failure scenario did not return 503 for every busy-adapter attempt'

$failureRetire = @($failureEntries | Where-Object { $_.operationId -eq 'partialUpdateCredential' })
Test-Claim ($failureRetire.Count -eq 0) `
    'the original credential was retired after an adapter exhausted its repoint attempts'
$failureRelease = @($failureEntries | Where-Object { $_.operationId -eq 'releaseToken' })
Test-Claim ($failureRelease.Count -eq 1) `
    "the persistent-failure session was released $($failureRelease.Count) time(s), expected once"
$failureViolations = @($failureEntries | Where-Object {
    $_.PSObject.Properties.Name -contains 'violation'
})
Test-Claim ($failureViolations.Count -eq 0) `
    'the persistent-failure scenario attempted an unsafe credential mutation'
if ($failureResult) {
    Test-Claim (-not [bool]$failureResult.Drained) `
        'the persistent-failure result claimed the old credential was drained'
    Test-Claim (-not [bool]$failureResult.Retired) `
        'the persistent-failure result claimed the old credential was retired'
}

# --- report --------------------------------------------------------------
Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue

if ($script:failures.Count -eq 0) {
    Write-Host "PASS - $($script:checks) checks"
    exit 0
}

Write-Host "FAIL - $($script:failures.Count) of $($script:checks) checks failed:"
foreach ($f in $script:failures) { Write-Host "  * $f" }
exit 1
