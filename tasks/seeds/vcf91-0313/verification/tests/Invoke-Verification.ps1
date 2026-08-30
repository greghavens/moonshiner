#Requires -Version 7.2
<#
    Protected verification for the VCF Automation credential rotation task.

    Starts the loopback mock from tools/mock, drives the module under test
    against it, then asserts the exact wire shape of every request the module
    made by reading the mock's JSON Lines request log.

    No live VMware endpoint is contacted. Everything happens on 127.0.0.1.

    Exit code 0 = all checks passed, 1 = at least one failed.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$ModulePath = Join-Path $RepoRoot 'src/VcfAutomation.CredentialRotation/VcfAutomation.CredentialRotation.psd1'
$MockScript = Join-Path $RepoRoot 'tools/mock/vcfa_mock_server.py'

$FixtureToken   = 'eyJhbGciOiJSUzI1NiJ9.vcfa-fixture-access-token.sig'
$CloudAccountId = 'ca-9f41d7b0-5c2e-4a18-bd93-0e7c6a1f4a22'
$ApiVersion     = '2021-07-15'
$NewKeyId       = 'svc-vcfa-provisioning-r2@vsphere.local'
$NewSecret      = 'N3wSecret!Applied-2026Q3-4f1a9c'
$OldSecret      = 'OldSecret!Rotate-Me-2026Q2'
$PreexistingId  = 'req-a41c9e02'

# ---------------------------------------------------------------- harness ---

$script:Failures = [System.Collections.Generic.List[string]]::new()
$script:Passes   = 0

function Test-That {
    param([string] $Name, [scriptblock] $Condition, [string] $Detail = '')
    $ok = $false
    $err = $null
    try { $ok = [bool](& $Condition) } catch { $ok = $false; $err = $_.Exception.Message }
    if ($ok) {
        $script:Passes++
        Write-Host "  PASS  $Name" -ForegroundColor Green
    } else {
        $msg = $Name
        if ($Detail) { $msg += " -- $Detail" }
        if ($err)    { $msg += " [error: $err]" }
        $script:Failures.Add($msg)
        Write-Host "  FAIL  $msg" -ForegroundColor Red
    }
}

function Get-KeySet {
    param([object] $Obj)
    # Two things an empty property bag does under `Set-StrictMode -Version
    # Latest`, and an object with no properties at all is precisely what the
    # empty-object cases below assert about. Reading `Name` straight off the
    # collection throws rather than yielding nothing, so it is made an array
    # first. And an empty array returned from a function unrolls to no output,
    # leaving `$null` where the caller asks for `.Count`, so the comma operator
    # keeps the array itself as the one value returned.
    if ($null -eq $Obj) { return ,@() }
    return ,@(@($Obj.PSObject.Properties) | ForEach-Object { $_.Name } | Sort-Object)
}

function Compare-KeySet {
    param([object] $Obj, [string[]] $Expected)
    $actual = Get-KeySet $Obj
    $want   = @($Expected | Sort-Object)
    if ($actual.Count -ne $want.Count) { return $false }
    for ($i = 0; $i -lt $want.Count; $i++) {
        if ($actual[$i] -ne $want[$i]) { return $false }
    }
    return $true
}

# ------------------------------------------------------------ mock startup ---

$python = $null
foreach ($candidate in 'python3', 'python') {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($found) { $python = $found.Source; break }
}
if (-not $python) { Write-Host 'FATAL: no python3 interpreter found.' -ForegroundColor Red; exit 1 }

$workDir  = Join-Path ([System.IO.Path]::GetTempPath()) ("vcfa-verify-" + [guid]::NewGuid().ToString('n'))
New-Item -ItemType Directory -Path $workDir -Force | Out-Null
$logPath  = Join-Path $workDir 'requests.jsonl'
$portFile = Join-Path $workDir 'port'
$mockErr  = Join-Path $workDir 'mock.stderr'

$mock = Start-Process -FilePath $python `
    -ArgumentList @($MockScript, '--port-file', $portFile, '--log', $logPath) `
    -PassThru -NoNewWindow -RedirectStandardError $mockErr

$port = $null
$deadline = [datetime]::UtcNow.AddSeconds(30)
while ([datetime]::UtcNow -lt $deadline) {
    if (Test-Path $portFile) {
        $raw = (Get-Content -Raw -Path $portFile -ErrorAction SilentlyContinue)
        if ($raw -and $raw.Trim()) { $port = [int]$raw.Trim(); break }
    }
    if ($mock.HasExited) { break }
    Start-Sleep -Milliseconds 100
}

if (-not $port) {
    Write-Host 'FATAL: mock server did not start.' -ForegroundColor Red
    if (Test-Path $mockErr) { Get-Content $mockErr | Write-Host }
    if (-not $mock.HasExited) { Stop-Process -Id $mock.Id -Force -ErrorAction SilentlyContinue }
    exit 1
}

$server = "http://127.0.0.1:$port"
Write-Host "Mock listening on $server" -ForegroundColor DarkGray

$result = $null
$runError = $null

try {
    # ------------------------------------------------- prerequisite wiring ---

    Write-Host "`n[1] Module loads against the VCF PowerCLI prerequisite" -ForegroundColor Cyan

    Import-Module $ModulePath -Force -ErrorAction Stop -WarningAction SilentlyContinue

    Test-That 'module exports New-VcfaUpdateCloudAccountSpecification' {
        [bool](Get-Command New-VcfaUpdateCloudAccountSpecification -ErrorAction SilentlyContinue)
    }
    Test-That 'module exports Invoke-VcfaCloudAccountCredentialRotation' {
        [bool](Get-Command Invoke-VcfaCloudAccountCredentialRotation -ErrorAction SilentlyContinue)
    }
    Test-That 'VMware.Sdk.Vcf prerequisite is loaded, not vendored' {
        $loaded = Get-Module -Name 'VMware.Sdk.Vcf.*'
        if (-not $loaded) { return $false }
        # every loaded SDK module must live outside this repository
        foreach ($m in $loaded) {
            if ($m.ModuleBase.StartsWith($RepoRoot, [StringComparison]::OrdinalIgnoreCase)) { return $false }
        }
        return $true
    } 'the module manifest must depend on the installed VMware.Sdk.Vcf modules and the repo must not contain a copy'

    # --------------------------------------------- builder omission semantics ---

    Write-Host "`n[2] UpdateCloudAccountSpecification omits unset optional fields" -ForegroundColor Cyan

    $regions = @(
        @{ externalRegionId = 'Datacenter:datacenter-3'; name = 'Frankfurt-DC1' }
    )

    # An unimplemented builder must produce failed checks, not abort the run.
    function Get-SpecJson {
        param([hashtable] $Arguments)
        $merged = @{ Name = 'acct'; CloudAccountProperties = @{ hostName = 'h' }; Regions = $regions }
        foreach ($k in $Arguments.Keys) { $merged[$k] = $Arguments[$k] }
        try { return (New-VcfaUpdateCloudAccountSpecification @merged).ToJson() } catch { return $null }
    }

    $minimalJson = Get-SpecJson @{}
    $minimalObj  = if ($minimalJson) { $minimalJson | ConvertFrom-Json } else { $null }

    Test-That 'required-only body carries exactly name, cloudAccountProperties, regions' {
        Compare-KeySet $minimalObj @('name', 'cloudAccountProperties', 'regions')
    } "got: $((Get-KeySet $minimalObj) -join ', ')"

    Test-That 'unset optionals are absent, not null / empty string / empty array' {
        $minimalJson -and
        $minimalJson -notmatch '"(description|privateKeyId|privateKey|customProperties|tags|certificateInfo|createDefaultZones|associatedCloudAccountIds|associatedMobilityCloudAccountIds)"'
    } "json was: $minimalJson"

    $withCredsJson = Get-SpecJson @{ PrivateKeyId = 'kid'; PrivateKey = 'secret' }
    $withCredsObj  = if ($withCredsJson) { $withCredsJson | ConvertFrom-Json } else { $null }

    Test-That 'supplied optionals appear, unsupplied ones still do not' {
        (Compare-KeySet $withCredsObj @('name', 'cloudAccountProperties', 'regions', 'privateKeyId', 'privateKey')) -and
        $withCredsObj.privateKeyId -eq 'kid' -and $withCredsObj.privateKey -eq 'secret'
    } "got: $((Get-KeySet $withCredsObj) -join ', ')"

    # The discriminator between "omit unset" and the wrong "omit falsy":
    # an explicitly supplied $false must be transmitted.
    Test-That 'an explicitly supplied $false optional IS transmitted' {
        $o = Get-SpecJson @{ CreateDefaultZones = $false } | ConvertFrom-Json
        ('createDefaultZones' -in (Get-KeySet $o)) -and ($o.createDefaultZones -eq $false)
    } 'omission must key off whether the caller bound the parameter, not off falsiness'

    Test-That 'an explicitly supplied empty string optional IS transmitted' {
        $o = Get-SpecJson @{ Description = '' } | ConvertFrom-Json
        ('description' -in (Get-KeySet $o)) -and ($o.description -eq '')
    } 'clearing a field is a legitimate, distinct intent from not mentioning it'

    Test-That 'an explicitly supplied empty array optional IS transmitted' {
        $o = Get-SpecJson @{ Tags = @() } | ConvertFrom-Json
        ('tags' -in (Get-KeySet $o)) -and (@($o.tags).Count -eq 0)
    } 'an explicit empty array clears an array field and must not be mistaken for omission'

    Test-That 'an explicitly supplied empty object optional IS transmitted' {
        $o = Get-SpecJson @{ CustomProperties = @{} } | ConvertFrom-Json
        ('customProperties' -in (Get-KeySet $o)) -and
        ((Get-KeySet $o.customProperties).Count -eq 0)
    } 'an explicit empty object clears an object field and must not be mistaken for omission'

    Test-That 'every remaining optional field uses its contract wire name' {
        $o = Get-SpecJson @{
            AssociatedCloudAccountIds = @()
            AssociatedMobilityCloudAccountIds = @{}
            CertificateInfo = @{}
        } | ConvertFrom-Json
        (Compare-KeySet $o @(
            'name',
            'cloudAccountProperties',
            'regions',
            'associatedCloudAccountIds',
            'associatedMobilityCloudAccountIds',
            'certificateInfo'
        )) -and
        (@($o.associatedCloudAccountIds).Count -eq 0) -and
        ((Get-KeySet $o.associatedMobilityCloudAccountIds).Count -eq 0) -and
        ((Get-KeySet $o.certificateInfo).Count -eq 0)
    } 'all optional parameters in the public builder must preserve explicit clearing values'

    Test-That 'region entries serialise to exactly externalRegionId and name' {
        Compare-KeySet $minimalObj.regions[0] @('externalRegionId', 'name')
    } "got: $(if ($minimalObj) { (Get-KeySet $minimalObj.regions[0]) -join ', ' })"

    # ------------------------------------------------------ the rotation run ---

    Write-Host "`n[3] Rotation runs end to end against the mock" -ForegroundColor Cyan

    try {
        $result = Invoke-VcfaCloudAccountCredentialRotation `
            -Server $server `
            -AccessToken $FixtureToken `
            -CloudAccountId $CloudAccountId `
            -NewPrivateKeyId $NewKeyId `
            -NewPrivateKey $NewSecret `
            -PollIntervalMilliseconds 20 `
            -ErrorAction Stop
    } catch {
        $runError = $_
    }

    Test-That 'rotation completed without throwing' { $null -eq $runError } `
        $(if ($runError) { $runError.Exception.Message } else { '' })

    Test-That 'rotation reports success' { $result -and $result.Succeeded -eq $true }
    Test-That 'rotation result carries exactly the documented properties' {
        Compare-KeySet $result @(
            'DrainedRequestIds',
            'RotationRequestId',
            'RotationStatus',
            'HealthCheckRequestId',
            'HealthCheckStatus',
            'Succeeded'
        )
    } "got: $((Get-KeySet $result) -join ', ')"
    Test-That 'rotation tracker reached FINISHED' { $result -and $result.RotationStatus -eq 'FINISHED' } `
        $(if ($result) { "got: $($result.RotationStatus)" } else { 'no result' })
    Test-That 'health check reached FINISHED' { $result -and $result.HealthCheckStatus -eq 'FINISHED' } `
        $(if ($result) { "got: $($result.HealthCheckStatus)" } else { 'no result' })
    Test-That "the pre-existing in-flight request $PreexistingId was drained" {
        $result -and ($PreexistingId -in @($result.DrainedRequestIds))
    } $(if ($result) { "got: $(@($result.DrainedRequestIds) -join ', ')" } else { 'no result' })

    # ------------------------------------------------------- wire inspection ---

    Write-Host "`n[4] Every request on the wire matches the contract" -ForegroundColor Cyan

    $entries = @(
        Get-Content -Path $logPath -ErrorAction SilentlyContinue |
            Where-Object { $_.Trim() } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )

    Test-That 'the mock recorded requests' { $entries.Count -gt 0 }

    Test-That 'no out-of-contract operation was called' {
        @($entries | Where-Object { $_.out_of_contract }).Count -eq 0
    } "offending: $((@($entries | Where-Object { $_.out_of_contract } | ForEach-Object { "$($_.method) $($_.path)" }) -join '; '))"

    Test-That 'no request was rejected by the mock' {
        @($entries | Where-Object { $_.status_code -ge 400 }).Count -eq 0
    } "offending: $((@($entries | Where-Object { $_.status_code -ge 400 } | ForEach-Object { "$($_.method) $($_.path) -> $($_.status_code): $($_.response_json.message)" }) -join '; '))"

    Test-That "every request pinned apiVersion=$ApiVersion" {
        @($entries | Where-Object { $_.query.apiVersion -ne $ApiVersion }).Count -eq 0
    } "offending: $((@($entries | Where-Object { $_.query.apiVersion -ne $ApiVersion } | ForEach-Object { $_.raw_target }) -join '; '))"

    Test-That 'every request carried the bearer token' {
        @($entries | Where-Object { $_.headers.authorization -ne "Bearer $FixtureToken" }).Count -eq 0
    }

    Test-That 'only the PATCH carried a request body' {
        @($entries | Where-Object {
            $_.operation_id -ne 'updateCloudAccountAsync' -and $_.body_raw
        }).Count -eq 0
    } 'the four read/manual-health operations have no request body in the contract'

    $patches = @($entries | Where-Object { $_.operation_id -eq 'updateCloudAccountAsync' })
    $gets    = @($entries | Where-Object { $_.operation_id -eq 'getCloudAccount' })
    $lists   = @($entries | Where-Object { $_.operation_id -eq 'getRequestTrackers' })
    $polls   = @($entries | Where-Object { $_.operation_id -eq 'getRequestTracker' })
    $health  = @($entries | Where-Object { $_.operation_id -eq 'runEndpointHealthCheck' })

    Test-That 'exactly one Update Cloud Account Async was issued' { $patches.Count -eq 1 } "got $($patches.Count)"
    Test-That 'the account was read before it was updated' {
        $gets.Count -ge 1 -and $patches.Count -eq 1 -and $gets[0].seq -lt $patches[0].seq
    }
    Test-That 'exactly one health check was issued' { $health.Count -eq 1 } "got $($health.Count)"

    # ----------------------------------------------- the drain-before-rotate ---

    Write-Host "`n[5] No in-flight request was stranded on the old secret" -ForegroundColor Cyan

    Test-That 'in-flight requests were discovered via Get Request Trackers before rotating' {
        $patches.Count -eq 1 -and @($lists | Where-Object { $_.seq -lt $patches[0].seq }).Count -ge 1
    } 'the client must list request trackers before it rotates'

    Test-That 'nothing was INPROGRESS at the moment the PATCH arrived' {
        $patches.Count -eq 1 -and @($patches[0].stranded_in_flight).Count -eq 0
    } $(if ($patches.Count -eq 1) { "stranded: $(@($patches[0].stranded_in_flight) -join ', ')" } else { '' })

    Test-That "the last observation of $PreexistingId before the PATCH showed a terminal status" {
        if ($patches.Count -ne 1) { return $false }
        $observations = @(
            $entries | Where-Object { $_.seq -lt $patches[0].seq } | ForEach-Object {
                if ($_.operation_id -eq 'getRequestTracker' -and $_.response_json -and $_.response_json.id -eq $PreexistingId) {
                    $_.response_json.status
                } elseif ($_.operation_id -eq 'getRequestTrackers' -and $_.response_json -and $_.response_json.content) {
                    ($_.response_json.content | Where-Object { $_.id -eq $PreexistingId }).status
                }
            }
        )
        $observations.Count -ge 1 -and $observations[-1] -in @('FINISHED', 'FAILED')
    } 'the rotation must not be sent until the pre-existing request has settled'

    Test-That "$PreexistingId reaching FAILED was recognized as terminal" {
        if ($patches.Count -ne 1) { return $false }
        $lastPoll = @(
            $polls | Where-Object {
                $_.seq -lt $patches[0].seq -and
                $_.response_json -and $_.response_json.id -eq $PreexistingId
            }
        ) | Select-Object -Last 1
        $lastPoll -and $lastPoll.response_json.status -eq 'FAILED'
    } 'FAILED is terminal and must not be polled until timeout'

    # --------------------------------------------------- PATCH body wire shape ---

    Write-Host "`n[6] The Update Cloud Account Async body is exactly right" -ForegroundColor Cyan

    $body = if ($patches.Count -eq 1) { $patches[0].body_json } else { $null }
    $account = if ($gets.Count -ge 1) { $gets[0].response_json } else { $null }

    Test-That 'PATCH declared Content-Type: application/json' {
        $patches.Count -eq 1 -and $patches[0].headers.'content-type' -match '^application/json'
    }

    Test-That 'body carries exactly the five intended fields' {
        Compare-KeySet $body @('name', 'cloudAccountProperties', 'regions', 'privateKeyId', 'privateKey')
    } "got: $((Get-KeySet $body) -join ', ')"

    Test-That 'unset optional fields were omitted from the wire, not sent empty' {
        $raw = if ($patches.Count -eq 1) { $patches[0].body_raw } else { '' }
        $raw -notmatch '"(description|customProperties|tags|certificateInfo|createDefaultZones|associatedCloudAccountIds|associatedMobilityCloudAccountIds)"'
    } "raw body was: $(if ($patches.Count -eq 1) { $patches[0].body_raw })"

    Test-That 'the new privateKeyId was sent' { $body -and $body.privateKeyId -eq $NewKeyId } `
        "got: $(if ($body) { $body.privateKeyId })"
    Test-That 'the new privateKey was sent' { $body -and $body.privateKey -eq $NewSecret }

    Test-That 'name was carried forward unchanged from the account' {
        $body -and $account -and $body.name -eq $account.name
    } "body '$(if ($body) { $body.name })' vs account '$(if ($account) { $account.name })'"

    Test-That 'cloudAccountProperties was carried forward unchanged' {
        if (-not $body -or -not $account) { return $false }
        if (-not (Compare-KeySet $body.cloudAccountProperties (Get-KeySet $account.cloudAccountProperties))) {
            return $false
        }
        foreach ($propertyName in (Get-KeySet $account.cloudAccountProperties)) {
            $actual = $body.cloudAccountProperties.PSObject.Properties[$propertyName].Value |
                ConvertTo-Json -Depth 10 -Compress
            $expected = $account.cloudAccountProperties.PSObject.Properties[$propertyName].Value |
                ConvertTo-Json -Depth 10 -Compress
            if ($actual -ne $expected) { return $false }
        }
        return $true
    } 'dropping hostName / certificate here would break the endpoint while rotating its password'

    Test-That 'regions was projected from the account enabledRegions' {
        if (-not $body -or -not $account) { return $false }
        $want = @($account.enabledRegions | ForEach-Object { "$($_.externalRegionId)|$($_.name)" } | Sort-Object)
        $got  = @($body.regions | ForEach-Object { "$($_.externalRegionId)|$($_.name)" } | Sort-Object)
        ($want.Count -gt 0) -and ($got.Count -eq $want.Count) -and (-not (Compare-Object $want $got))
    } "body regions: $(if ($body) { ($body.regions | ConvertTo-Json -Compress -Depth 5) })"

    Test-That 'each region entry carries only externalRegionId and name' {
        if (-not $body) { return $false }
        foreach ($r in @($body.regions)) {
            if (-not (Compare-KeySet $r @('externalRegionId', 'name'))) { return $false }
        }
        return $true
    } 'Region from the response is not RegionSpecification for the request'

    # --------------------------------------------------------- secret hygiene ---

    Write-Host "`n[7] The secret went only where it belongs" -ForegroundColor Cyan

    Test-That 'the new secret never appeared in a URL or query string' {
        @($entries | Where-Object { $_.raw_target -like "*$NewSecret*" }).Count -eq 0
    }

    Test-That 'the new secret never appeared in a request header' {
        foreach ($e in $entries) {
            foreach ($v in $e.headers.PSObject.Properties.Value) {
                if ("$v" -like "*$NewSecret*") { return $false }
            }
        }
        return $true
    }

    Test-That 'the new secret was sent on exactly one request' {
        @($entries | Where-Object { $_.body_raw -like "*$NewSecret*" }).Count -eq 1
    }

    Test-That 'the old secret was never transmitted' {
        @($entries | Where-Object { $_.body_raw -like "*$OldSecret*" -or $_.raw_target -like "*$OldSecret*" }).Count -eq 0
    }

    # ------------------------------------------------------- async completion ---

    Write-Host "`n[8] Async operations were followed to a terminal status" -ForegroundColor Cyan

    $rotationTrackerId = if ($patches.Count -eq 1 -and $patches[0].response_json) { $patches[0].response_json.id } else { $null }

    Test-That 'the rotation tracker was polled after the 202' {
        $rotationTrackerId -and
        @($polls | Where-Object { $_.path -like "*/$rotationTrackerId" -and $_.seq -gt $patches[0].seq }).Count -ge 1
    } '202 Accepted is not success'

    Test-That 'the health check was issued only after the rotation finished' {
        if (-not $rotationTrackerId -or $health.Count -ne 1) { return $false }
        $settled = @(
            $polls | Where-Object {
                $_.path -like "*/$rotationTrackerId" -and $_.seq -lt $health[0].seq -and
                $_.response_json -and $_.response_json.status -eq 'FINISHED'
            }
        )
        $settled.Count -ge 1
    } 'health-checking an endpoint whose update is still in flight proves nothing'

    Test-That 'the health check tracker was polled to a terminal status' {
        if ($health.Count -ne 1 -or -not $health[0].response_json) { return $false }
        $hid = $health[0].response_json.id
        @($polls | Where-Object {
            $_.path -like "*/$hid" -and $_.response_json -and $_.response_json.status -in @('FINISHED', 'FAILED')
        }).Count -ge 1
    }

    Test-That 'the health check did not send periodicHealthCheckId for a manual check' {
        $health.Count -eq 1 -and -not ($health[0].query.PSObject.Properties.Name -contains 'periodicHealthCheckId')
    } 'an unset optional query parameter must be omitted too'
}
finally {
    if ($mock -and -not $mock.HasExited) {
        Stop-Process -Id $mock.Id -Force -ErrorAction SilentlyContinue
    }
}

# ------------------------------------------------------------------ summary ---

Write-Host ''
Write-Host ('-' * 72)
$total = $script:Passes + $script:Failures.Count
if ($script:Failures.Count -eq 0) {
    Write-Host "All $total checks passed." -ForegroundColor Green
    Write-Host "Request log: $logPath" -ForegroundColor DarkGray
    exit 0
}

Write-Host "$($script:Passes)/$total checks passed. $($script:Failures.Count) failed:" -ForegroundColor Red
foreach ($f in $script:Failures) { Write-Host "  - $f" -ForegroundColor Red }
Write-Host "Request log: $logPath" -ForegroundColor DarkGray
exit 1
