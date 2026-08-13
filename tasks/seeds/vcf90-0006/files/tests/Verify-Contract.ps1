<#
    PROTECTED VERIFICATION -- do not modify.

    Offline, deterministic verification of the gated host commission workflow.
    No live VMware endpoint is contacted: every HTTP call goes to the loopback
    mock started by this script on 127.0.0.1.

    Run:  pwsh -NoProfile -File tests/Verify-Contract.ps1
    Exit: 0 when every assertion passes, 1 otherwise.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ModuleManifest = Join-Path $RepoRoot 'src/VcfHostCommission/VcfHostCommission.psd1'
$MockScript = Join-Path $RepoRoot 'tools/Start-SddcManagerMock.ps1'
$ContractFile = Join-Path $RepoRoot 'docs/contract.json'
$SourcesFile = Join-Path $RepoRoot 'docs/official_sources.json'
$WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ("vcf-verify-" + [guid]::NewGuid().ToString('n'))
$null = New-Item -ItemType Directory -Path $WorkDir -Force

# The expected upstream commit is stored as a digest so that resolving the
# vmware/vcf-api-specs 9.0.0.0 tag requires real research rather than reading
# the answer out of this file.
$ExpectedCommitDigest = '33081e6a37b93a99c88b486516bee45550749d8c6e99fafce81f0623215b3aa4'
$WrongRevisionDigests = @{
    '15c2eff25b44c0b0e05a0d69cd19ea9551ceb6883136f32e48c3ef72a6a80102' = 'the 9.1.0.0 tag of the same file'
}

$SpecPath = 'specifications/sddc-manager/sddc-manager-openapi.json'
$SpecTag = '9.0.0.0'
$ExpectedOperationIds = @('commissionHosts', 'createToken', 'getHostCommissionValidationByID', 'validateHostCommissionSpec')
$MockAccessToken = 'mock-access-token'

$script:Failures = New-Object System.Collections.Generic.List[string]
$script:Checks = 0
$script:MockProcesses = New-Object System.Collections.Generic.List[object]

function Write-Section { param([string]$Name) Write-Host "`n== $Name" -ForegroundColor Cyan }

function Assert-True {
    param([bool]$Condition, [string]$Message)
    $script:Checks++
    if ($Condition) { Write-Host "  PASS  $Message" }
    else { Write-Host "  FAIL  $Message" -ForegroundColor Red; $script:Failures.Add($Message) }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Message)
    $ok = ($null -eq $Expected -and $null -eq $Actual) -or ($null -ne $Expected -and $Expected.Equals($Actual))
    Assert-True -Condition $ok -Message "$Message (expected '$Expected', got '$Actual')"
}

function Assert-SetEqual {
    param([string[]]$Expected, [string[]]$Actual, [string]$Message)
    $e = @($Expected | Sort-Object) -join ','
    $a = @($Actual | Sort-Object) -join ','
    Assert-True -Condition ($e -eq $a) -Message "$Message (expected [$e], got [$a])"
}

function Assert-SequenceEqual {
    param([string[]]$Expected, [string[]]$Actual, [string]$Message)
    $e = @($Expected) -join ','
    $a = @($Actual) -join ','
    Assert-True -Condition ($e -ceq $a) -Message "$Message (expected [$e], got [$a])"
}

function Get-Sha256Hex {
    param([string]$Value)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return -join ($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Value)) | ForEach-Object { $_.ToString('x2') }) }
    finally { $sha.Dispose() }
}

function Get-FreePort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    $listener.Stop()
    return $port
}

function Start-Mock {
    param([Parameter(Mandatory)][string]$Scenario, [Parameter(Mandatory)][string]$Tag)

    $port = Get-FreePort
    $log = Join-Path $WorkDir "$Tag.jsonl"
    Set-Content -LiteralPath $log -Value '{"stale":true}' -NoNewline -Encoding utf8
    $arguments = @(
        '-NoProfile', '-NonInteractive', '-File', $MockScript,
        '-Port', $port, '-RequestLogPath', $log, '-Scenario', $Scenario
    )
    $process = Start-Process -FilePath (Get-Process -Id $PID).Path -ArgumentList $arguments -PassThru `
        -RedirectStandardOutput (Join-Path $WorkDir "$Tag.out") -RedirectStandardError (Join-Path $WorkDir "$Tag.err")
    $script:MockProcesses.Add($process)

    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline) {
        try {
            $probe = [System.Net.Sockets.TcpClient]::new()
            $probe.Connect('127.0.0.1', $port)
            $probe.Close()
            return [pscustomobject]@{ Port = $port; LogPath = $log; BaseUrl = "http://127.0.0.1:$port"; Process = $process }
        }
        catch { Start-Sleep -Milliseconds 200 }
    }
    $stderr = if (Test-Path (Join-Path $WorkDir "$Tag.err")) { Get-Content -LiteralPath (Join-Path $WorkDir "$Tag.err") -Raw } else { '' }
    throw "The mock did not begin listening on port $port for scenario '$Scenario'. stderr: $stderr"
}

function Stop-Mock {
    param($Mock)
    if ($Mock -and $Mock.Process -and -not $Mock.Process.HasExited) {
        Stop-Process -Id $Mock.Process.Id -Force -ErrorAction SilentlyContinue
    }
}

function Read-RequestLog {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    $entries = New-Object System.Collections.Generic.List[object]
    foreach ($line in (Get-Content -LiteralPath $Path)) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $entries.Add(($line | ConvertFrom-Json))
    }
    return $entries.ToArray()
}

function Get-Header {
    param($Entry, [string]$Name)
    foreach ($property in $Entry.headers.PSObject.Properties) {
        if ($property.Name -ieq $Name) { return [string]$property.Value }
    }
    return $null
}

function Get-PropertyNames { param($Object) return @($Object.PSObject.Properties.Name) }

function Invoke-Probe {
    param([string]$Method, [string]$Uri, [string]$Body)
    $arguments = @{ Method = $Method; Uri = $Uri; SkipHttpErrorCheck = $true; TimeoutSec = 30 }
    if ($PSBoundParameters.ContainsKey('Body')) {
        $arguments['Body'] = $Body
        $arguments['ContentType'] = 'application/json'
    }
    return Invoke-WebRequest @arguments
}

try {
    # ------------------------------------------------------------------ layout
    Write-Section 'Deliverables present'
    Assert-True -Condition (Test-Path -LiteralPath $ContractFile) -Message 'docs/contract.json exists'
    Assert-True -Condition (Test-Path -LiteralPath $SourcesFile) -Message 'docs/official_sources.json exists'
    Assert-True -Condition (Test-Path -LiteralPath $ModuleManifest) -Message 'src/VcfHostCommission/VcfHostCommission.psd1 exists'
    Assert-True -Condition (Test-Path -LiteralPath (Join-Path $RepoRoot 'src/VcfHostCommission/VcfHostCommission.psm1')) -Message 'src/VcfHostCommission/VcfHostCommission.psm1 exists'
    Assert-True -Condition (Test-Path -LiteralPath $MockScript) -Message 'tools/Start-SddcManagerMock.ps1 exists'

    if ($script:Failures.Count -gt 0) { throw 'Required deliverables are missing; stopping.' }

    Assert-True -Condition ($null -ne (Get-Module -ListAvailable -Name 'VMware.Sdk.Vcf.SddcManager')) `
        -Message 'prerequisite module VMware.Sdk.Vcf.SddcManager is installed in the environment'
    Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $RepoRoot 'src/VMware.Sdk.Vcf.SddcManager'))) `
        -Message 'the VMware SDK is not vendored into src/'

    # -------------------------------------------------------- official sources
    Write-Section 'docs/official_sources.json'
    $sources = Get-Content -LiteralPath $SourcesFile -Raw | ConvertFrom-Json
    Assert-True -Condition ($null -ne $sources.sources -and @($sources.sources).Count -ge 1) -Message 'official_sources.json has a sources array'

    $specSource = @($sources.sources) | Where-Object { $_.path -eq $SpecPath } | Select-Object -First 1
    Assert-True -Condition ($null -ne $specSource) -Message "a source records the spec path '$SpecPath'"

    if ($specSource) {
        Assert-Equal -Expected 'https://github.com/vmware/vcf-api-specs' -Actual ([string]$specSource.repository) -Message 'source repository is vmware/vcf-api-specs'
        Assert-Equal -Expected 'Apache-2.0' -Actual ([string]$specSource.license) -Message 'source license is Apache-2.0'
        Assert-Equal -Expected $SpecTag -Actual ([string]$specSource.tag) -Message 'source tag is 9.0.0.0'

        $commit = ([string]$specSource.commit).Trim().ToLowerInvariant()
        Assert-True -Condition ($commit -match '^[0-9a-f]{40}$') -Message 'source commit is a full 40-character sha'
        $digest = Get-Sha256Hex -Value $commit
        if ($WrongRevisionDigests.ContainsKey($digest)) {
            Assert-True -Condition $false -Message "source commit is the 9.0.0.0 tag commit, not $($WrongRevisionDigests[$digest])"
        }
        else {
            Assert-True -Condition ($digest -eq $ExpectedCommitDigest) -Message 'source commit is the commit the vmware/vcf-api-specs 9.0.0.0 tag points at'
        }

        $sourceUri = [uri]([string]$specSource.url)
        Assert-True -Condition ($sourceUri.Scheme -eq 'https' -and $sourceUri.Host -in @('github.com', 'raw.githubusercontent.com')) `
            -Message 'source url is an HTTPS GitHub permalink'
        Assert-True -Condition ($sourceUri.AbsoluteUri -like "*$commit*" -and $sourceUri.AbsoluteUri -like "*$SpecPath") `
            -Message 'source url pins the same commit and spec path'
        Assert-SequenceEqual -Expected @($ExpectedOperationIds | Sort-Object) -Actual @($specSource.operationIds) `
            -Message 'source records the operationIds in alphabetical order'
    }

    # ---------------------------------------------------------------- contract
    Write-Section 'docs/contract.json'
    $contract = Get-Content -LiteralPath $ContractFile -Raw | ConvertFrom-Json
    Assert-Equal -Expected 'https://github.com/vmware/vcf-api-specs' -Actual ([string]$contract.source.repository) `
        -Message 'contract source repository is vmware/vcf-api-specs'
    Assert-Equal -Expected $SpecPath -Actual ([string]$contract.source.path) -Message 'contract source path is the SDDC Manager spec'
    Assert-Equal -Expected $SpecTag -Actual ([string]$contract.source.tag) -Message 'contract source tag is 9.0.0.0'
    Assert-Equal -Expected ([string]$specSource.commit).Trim().ToLowerInvariant() -Actual ([string]$contract.source.commit).Trim().ToLowerInvariant() `
        -Message 'contract and official_sources pin the same commit'
    Assert-Equal -Expected '9.0.0.0' -Actual ([string]$contract.source.infoVersion) -Message 'contract records the spec info.version'
    Assert-Equal -Expected '3.0.1' -Actual ([string]$contract.source.openapi) -Message 'contract records the spec openapi version'

    $expectedOperations = @(
        @{ operationId = 'createToken'; method = 'POST'; path = '/v1/tokens'; successStatus = 201; requestSchema = 'TokenCreationSpec'; requestIsArray = $false; responseSchema = 'TokenPair' },
        @{ operationId = 'validateHostCommissionSpec'; method = 'POST'; path = '/v1/hosts/validations'; successStatus = 202; requestSchema = 'HostCommissionSpec'; requestIsArray = $true; responseSchema = 'Validation' },
        @{ operationId = 'getHostCommissionValidationByID'; method = 'GET'; path = '/v1/hosts/validations/{id}'; successStatus = 202; requestSchema = $null; requestIsArray = $false; responseSchema = 'Validation' },
        @{ operationId = 'commissionHosts'; method = 'POST'; path = '/v1/hosts'; successStatus = 202; requestSchema = 'HostCommissionSpec'; requestIsArray = $true; responseSchema = 'Task' }
    )

    Assert-SetEqual -Expected $ExpectedOperationIds -Actual @($contract.operations.operationId) -Message 'contract names exactly the four operations used'
    foreach ($expected in $expectedOperations) {
        $actual = @($contract.operations) | Where-Object { $_.operationId -eq $expected.operationId } | Select-Object -First 1
        if (-not $actual) { Assert-True -Condition $false -Message "contract defines $($expected.operationId)"; continue }
        Assert-Equal -Expected $expected.method -Actual ([string]$actual.method) -Message "$($expected.operationId) method"
        Assert-Equal -Expected $expected.path -Actual ([string]$actual.path) -Message "$($expected.operationId) path"
        Assert-Equal -Expected $expected.successStatus -Actual ([int]$actual.successStatus) -Message "$($expected.operationId) success status"
        Assert-Equal -Expected $expected.responseSchema -Actual ([string]$actual.responseSchema) -Message "$($expected.operationId) response schema"
        Assert-Equal -Expected $expected.requestIsArray -Actual ([bool]$actual.requestIsArray) -Message "$($expected.operationId) requestIsArray"
        $requestSchema = if ($null -eq $actual.requestSchema) { $null } else { [string]$actual.requestSchema }
        Assert-Equal -Expected $expected.requestSchema -Actual $requestSchema -Message "$($expected.operationId) request schema"
    }

    $deprecatedCount = (@($contract.operations) | Where-Object { $_.operationId -eq 'validateCommissionHosts' -or $_.path -eq '/v1/hosts/validations/commissions' } | Measure-Object).Count
    Assert-True -Condition ($deprecatedCount -eq 0) -Message 'contract does not use the deprecated validateCommissionHosts operation'

    Assert-SequenceEqual -Expected @('fqdn', 'networkPoolId', 'password', 'storageType', 'username') `
        -Actual @($contract.schemas.HostCommissionSpec.required) -Message 'HostCommissionSpec required properties'
    Assert-SequenceEqual -Expected @('networkPoolName', 'sshThumbprint', 'sslThumbprint', 'vvolStorageProtocolType') `
        -Actual @($contract.schemas.HostCommissionSpec.optional) -Message 'HostCommissionSpec optional properties'
    Assert-True -Condition (@($contract.schemas.TokenCreationSpec.required).Count -eq 0) -Message 'TokenCreationSpec has no required properties'
    Assert-SequenceEqual -Expected @('apiKey', 'idToken', 'password', 'username') `
        -Actual @($contract.schemas.TokenCreationSpec.optional) -Message 'TokenCreationSpec optional properties'

    # ------------------------------------------------------- mock scope + logs
    Write-Section 'Mock is pinned to the contract and records real requests'
    $probeMock = Start-Mock -Scenario 'PrecheckPasses' -Tag 'probe'
    try {
        $probeBody = '{"username":"verifier@vsphere.local","password":"probe-secret"}'
        $tokenResponse = Invoke-Probe -Method 'POST' -Uri "$($probeMock.BaseUrl)/v1/tokens" -Body $probeBody
        Assert-Equal -Expected 201 -Actual ([int]$tokenResponse.StatusCode) -Message 'mock answers createToken with the contract status 201'
        $tokenPayload = $tokenResponse.Content | ConvertFrom-Json
        Assert-Equal -Expected $MockAccessToken -Actual ([string]$tokenPayload.accessToken) -Message 'mock returns the documented access token'
        Assert-True -Condition (-not [string]::IsNullOrWhiteSpace([string]$tokenPayload.refreshToken.id)) `
            -Message 'mock token response also carries a refresh token'

        foreach ($outOfScope in @(
                @{ Method = 'GET'; Path = '/v1/hosts' },
                @{ Method = 'DELETE'; Path = '/v1/hosts' },
                @{ Method = 'GET'; Path = '/v1/sddc-manager' },
                @{ Method = 'POST'; Path = '/v1/hosts/validations/commissions' },
                @{ Method = 'GET'; Path = '/v1/domains?alpha=one%20two&alpha=three' })) {
            $response = Invoke-Probe -Method $outOfScope.Method -Uri "$($probeMock.BaseUrl)$($outOfScope.Path)"
            Assert-Equal -Expected 404 -Actual ([int]$response.StatusCode) -Message "mock refuses $($outOfScope.Method) $($outOfScope.Path); it is not a contract operation"
        }

        $probeLog = Read-RequestLog -Path $probeMock.LogPath
        $recordedProbe = @($probeLog) | Where-Object { $_.method -eq 'POST' -and $_.path -eq '/v1/tokens' } | Select-Object -First 1
        Assert-True -Condition ($null -ne $recordedProbe) -Message 'request log captured the probe request'
        if ($recordedProbe) {
            Assert-SetEqual -Expected @('method', 'path', 'query', 'body', 'headers') `
                -Actual (Get-PropertyNames -Object $recordedProbe) -Message 'each request log line has exactly the required keys'
            Assert-Equal -Expected $probeBody -Actual ([string]$recordedProbe.body) -Message 'request log records the request body verbatim'
        }
        $queryProbe = @($probeLog) | Where-Object { $_.path -eq '/v1/domains' } | Select-Object -First 1
        Assert-Equal -Expected 'alpha=one%20two&alpha=three' -Actual ([string]$queryProbe.query) `
            -Message 'request log records the raw query string without the path or question-mark delimiter'
        Assert-Equal -Expected 6 -Actual (@($probeLog).Count) -Message 'request log records every request, in-contract and out-of-contract'
    }
    finally { Stop-Mock $probeMock }

    # ------------------------------------------------------------- happy path
    Write-Section 'Precheck passes: the mutating call is made with the exact contract wire shape'
    Import-Module $ModuleManifest -Force
    Assert-True -Condition ($null -ne (Get-Command -Name 'Invoke-VcfHostCommissionWorkflow' -ErrorAction SilentlyContinue)) `
        -Message 'the module exports Invoke-VcfHostCommissionWorkflow'
    Assert-SequenceEqual -Expected @('Invoke-VcfHostCommissionWorkflow') `
        -Actual @(Get-Command -Module VcfHostCommission -CommandType Function | Select-Object -ExpandProperty Name) `
        -Message 'the module exports only the requested workflow function'

    $hostSpec = @(
        @{
            Fqdn          = 'esx-11.vcf.lab'
            Username      = 'root'
            Password      = 'VMw@re1!VMw@re1!'
            StorageType   = 'VSAN'
            NetworkPoolId = '2e8c8e3f-1c8a-4f2b-8a9d-0f3c9f0a1234'
        },
        @{
            Fqdn          = 'esx-13.vcf.lab'
            Username      = 'root'
            Password      = 'VMw@re1!VMw@re1!'
            StorageType   = 'VSAN'
            NetworkPoolId = '2e8c8e3f-1c8a-4f2b-8a9d-0f3c9f0a1234'
        }
    )

    $passMock = Start-Mock -Scenario 'PrecheckPasses' -Tag 'pass'
    try {
        $result = Invoke-VcfHostCommissionWorkflow -BaseUrl $passMock.BaseUrl -Username 'administrator@vsphere.local' `
            -Password 'VMw@re1!VMw@re1!' -HostSpec $hostSpec
        Assert-Equal -Expected $true -Actual ([bool]$result.Committed) -Message 'workflow reports the hosts were committed'
        Assert-Equal -Expected 'task-0001' -Actual ([string]$result.Task.Id) -Message 'workflow returns the commission task'
        Assert-Equal -Expected 'SUCCEEDED' -Actual ([string]$result.Validation.ResultStatus) -Message 'workflow returns the precheck result'
        Assert-Equal -Expected 'validation-0001' -Actual ([string]$result.Validation.Id) -Message 'workflow returns the precheck validation id'
        Assert-Equal -Expected 'Host commission validation' -Actual ([string]$result.Validation.Description) -Message 'workflow returns the precheck description'
        Assert-Equal -Expected 'Commissioning Hosts' -Actual ([string]$result.Task.Name) -Message 'workflow returns the fixed commission task name'
        Assert-Equal -Expected 'HOST_COMMISSION' -Actual ([string]$result.Task.Type) -Message 'workflow returns the fixed commission task type'
        Assert-Equal -Expected 'IN_PROGRESS' -Actual ([string]$result.Task.Status) -Message 'workflow returns the fixed commission task status'
        Assert-Equal -Expected '2026-01-01T00:00:00.000Z' -Actual ([string]$result.Task.CreationTimestamp) -Message 'workflow returns the fixed commission task timestamp'
        Assert-True -Condition (-not [string]::IsNullOrWhiteSpace([string]$result.Reason)) -Message 'workflow explains the successful submission'
        Assert-SetEqual -Expected @('Validation', 'Committed', 'Task', 'Reason') -Actual (Get-PropertyNames -Object $result) `
            -Message 'workflow result has the requested public properties'
    }
    finally { Stop-Mock $passMock }

    $passLog = Read-RequestLog -Path $passMock.LogPath
    Assert-Equal -Expected 3 -Actual (@($passLog).Count) -Message 'a passing precheck issues exactly three requests'

    if (@($passLog).Count -eq 3) {
        $tokenCall, $validateCall, $commissionCall = $passLog

        Assert-Equal -Expected 'POST' -Actual ([string]$tokenCall.method) -Message 'call 1 method'
        Assert-Equal -Expected '/v1/tokens' -Actual ([string]$tokenCall.path) -Message 'call 1 path is createToken'
        Assert-Equal -Expected '' -Actual ([string]$tokenCall.query) -Message 'call 1 carries no query string'
        Assert-True -Condition ((Get-Header -Entry $tokenCall -Name 'Content-Type') -like 'application/json*') -Message 'call 1 sends JSON'
        Assert-True -Condition ($null -eq (Get-Header -Entry $tokenCall -Name 'Authorization')) -Message 'call 1 is unauthenticated; it is the call that mints the token'

        $tokenBody = [string]$tokenCall.body | ConvertFrom-Json
        Assert-SetEqual -Expected @('username', 'password') -Actual (Get-PropertyNames -Object $tokenBody) `
            -Message 'TokenCreationSpec sends only the properties that were set'
        Assert-Equal -Expected 'administrator@vsphere.local' -Actual ([string]$tokenBody.username) -Message 'TokenCreationSpec username'
        foreach ($omitted in @('apiKey', 'idToken')) {
            Assert-True -Condition (([string]$tokenCall.body) -notmatch [regex]::Escape($omitted)) `
                -Message "unset TokenCreationSpec property '$omitted' is omitted, not sent empty"
        }

        Assert-Equal -Expected 'POST' -Actual ([string]$validateCall.method) -Message 'call 2 method'
        Assert-Equal -Expected '/v1/hosts/validations' -Actual ([string]$validateCall.path) -Message 'call 2 path is validateHostCommissionSpec'
        Assert-Equal -Expected "Bearer $MockAccessToken" -Actual (Get-Header -Entry $validateCall -Name 'Authorization') -Message 'call 2 presents the bearer token from createToken'
        Assert-True -Condition ((Get-Header -Entry $validateCall -Name 'Content-Type') -like 'application/json*') -Message 'call 2 sends JSON'

        $validateBody = [string]$validateCall.body | ConvertFrom-Json
        Assert-True -Condition (([string]$validateCall.body).TrimStart().StartsWith('[')) -Message 'validateHostCommissionSpec sends an array of HostCommissionSpec'
        Assert-Equal -Expected 2 -Actual (@($validateBody).Count) -Message 'the array preserves all supplied host specs'
        Assert-SetEqual -Expected @('fqdn', 'username', 'password', 'storageType', 'networkPoolId') `
            -Actual (Get-PropertyNames -Object (@($validateBody)[0])) -Message 'HostCommissionSpec sends only the properties that were set'
        Assert-Equal -Expected 'esx-11.vcf.lab' -Actual ([string](@($validateBody)[0]).fqdn) -Message 'HostCommissionSpec fqdn'
        Assert-Equal -Expected 'VSAN' -Actual ([string](@($validateBody)[0]).storageType) -Message 'HostCommissionSpec storageType'
        Assert-Equal -Expected '2e8c8e3f-1c8a-4f2b-8a9d-0f3c9f0a1234' -Actual ([string](@($validateBody)[0]).networkPoolId) -Message 'HostCommissionSpec networkPoolId'
        Assert-Equal -Expected 'esx-13.vcf.lab' -Actual ([string](@($validateBody)[1]).fqdn) -Message 'the second HostCommissionSpec is preserved'
        foreach ($omitted in @('networkPoolName', 'sshThumbprint', 'sslThumbprint', 'vvolStorageProtocolType')) {
            Assert-True -Condition (([string]$validateCall.body) -notmatch [regex]::Escape($omitted)) `
                -Message "unset HostCommissionSpec property '$omitted' is omitted, not sent empty"
        }
        Assert-True -Condition (([string]$validateCall.body) -notmatch ':\s*null') -Message 'no property is sent as null'
        Assert-True -Condition (([string]$validateCall.body) -notmatch ':\s*""') -Message 'no property is sent as an empty string'

        Assert-Equal -Expected 'POST' -Actual ([string]$commissionCall.method) -Message 'call 3 method'
        Assert-Equal -Expected '/v1/hosts' -Actual ([string]$commissionCall.path) -Message 'call 3 path is commissionHosts'
        Assert-Equal -Expected "Bearer $MockAccessToken" -Actual (Get-Header -Entry $commissionCall -Name 'Authorization') -Message 'call 3 presents the bearer token'
        Assert-Equal -Expected ([string]$validateCall.body) -Actual ([string]$commissionCall.body) -Message 'commissionHosts sends byte-identical specs to the ones the precheck cleared'
    }

    # ------------------------------------------------------------------- gate
    Write-Section 'Precheck fails: nothing is changed'
    $failMock = Start-Mock -Scenario 'PrecheckFails' -Tag 'fail'
    try {
        $blocked = Invoke-VcfHostCommissionWorkflow -BaseUrl $failMock.BaseUrl -Username 'administrator@vsphere.local' `
            -Password 'VMw@re1!VMw@re1!' -HostSpec $hostSpec
        Assert-Equal -Expected $false -Actual ([bool]$blocked.Committed) -Message 'workflow reports nothing was committed'
        Assert-True -Condition ($null -eq $blocked.Task) -Message 'workflow returns no commission task'
        Assert-Equal -Expected 'FAILED' -Actual ([string]$blocked.Validation.ResultStatus) -Message 'workflow surfaces the failed precheck result'
        Assert-True -Condition (@($blocked.Validation.ValidationChecks).Count -ge 1) -Message 'failed scenario includes a failed validation check'
        Assert-Equal -Expected 'FAILED' -Actual ([string](@($blocked.Validation.ValidationChecks)[0]).ResultStatus) `
            -Message 'failed scenario exposes the failed validation check result'
        Assert-True -Condition (-not [string]::IsNullOrWhiteSpace([string]$blocked.Reason)) -Message 'workflow explains why it stopped'
    }
    finally { Stop-Mock $failMock }

    $failLog = Read-RequestLog -Path $failMock.LogPath
    Assert-Equal -Expected 0 -Actual (@($failLog) | Where-Object { $_.method -eq 'POST' -and $_.path -eq '/v1/hosts' } | Measure-Object).Count `
        -Message 'the mutating commissionHosts call is never issued when the precheck fails'
    Assert-Equal -Expected 1 -Actual (@($failLog) | Where-Object { $_.method -eq 'POST' -and $_.path -eq '/v1/hosts/validations' } | Measure-Object).Count `
        -Message 'the precheck itself was issued exactly once'
    Assert-Equal -Expected 2 -Actual (@($failLog).Count) -Message 'a failing precheck issues exactly two requests'

    # ---------------------------------------------------------------- polling
    Write-Section 'Precheck is asynchronous: the workflow polls before it decides'
    $pollMock = Start-Mock -Scenario 'PrecheckPendingThenPasses' -Tag 'poll'
    try {
        $polled = Invoke-VcfHostCommissionWorkflow -BaseUrl $pollMock.BaseUrl -Username 'administrator@vsphere.local' `
            -Password 'VMw@re1!VMw@re1!' -HostSpec $hostSpec
        Assert-Equal -Expected $true -Actual ([bool]$polled.Committed) -Message 'workflow commits once the precheck finishes SUCCEEDED'
    }
    finally { Stop-Mock $pollMock }

    $pollLog = Read-RequestLog -Path $pollMock.LogPath
    $polls = @($pollLog) | Where-Object { $_.method -eq 'GET' -and $_.path -like '/v1/hosts/validations/*' }
    Assert-True -Condition (@($polls).Count -ge 2) -Message 'workflow polls getHostCommissionValidationByID while the precheck is IN_PROGRESS'
    Assert-Equal -Expected '/v1/hosts/validations/validation-0001' -Actual ([string](@($polls)[0]).path) -Message 'polls address the validation id returned by the precheck'
    Assert-Equal -Expected "Bearer $MockAccessToken" -Actual (Get-Header -Entry (@($polls)[0]) -Name 'Authorization') -Message 'polls present the bearer token'

    $mutations = @($pollLog) | Where-Object { $_.method -eq 'POST' -and $_.path -eq '/v1/hosts' }
    Assert-Equal -Expected 1 -Actual (@($mutations).Count) -Message 'commissionHosts is issued exactly once, after the precheck settles'
    $mutationIndex = [array]::IndexOf(@($pollLog), @($mutations)[0])
    $lastPollIndex = [array]::IndexOf(@($pollLog), @($polls)[-1])
    Assert-True -Condition ($mutationIndex -gt $lastPollIndex) -Message 'commissionHosts is issued only after the final poll'

    # ---------------------------------------------------------------- timeout
    Write-Section 'A precheck that has not settled by the deadline is blocked'
    $timeoutMock = Start-Mock -Scenario 'PrecheckPendingThenPasses' -Tag 'timeout'
    try {
        $timedOut = Invoke-VcfHostCommissionWorkflow -BaseUrl $timeoutMock.BaseUrl -Username 'administrator@vsphere.local' `
            -Password 'VMw@re1!VMw@re1!' -HostSpec $hostSpec -PollIntervalSeconds 0 -TimeoutSeconds 0
        Assert-Equal -Expected $false -Actual ([bool]$timedOut.Committed) -Message 'a timed-out precheck is not committed'
        Assert-True -Condition ($null -eq $timedOut.Task) -Message 'a timed-out precheck returns no commission task'
        Assert-Equal -Expected 'IN_PROGRESS' -Actual ([string]$timedOut.Validation.ExecutionStatus) `
            -Message 'timeout returns the latest validation state'
        Assert-True -Condition (-not [string]::IsNullOrWhiteSpace([string]$timedOut.Reason)) -Message 'timeout has a human-readable reason'
    }
    finally { Stop-Mock $timeoutMock }

    $timeoutLog = Read-RequestLog -Path $timeoutMock.LogPath
    Assert-Equal -Expected 0 -Actual (@($timeoutLog) | Where-Object { $_.method -eq 'POST' -and $_.path -eq '/v1/hosts' } | Measure-Object).Count `
        -Message 'the mutating call is never issued after timeout'

    # -------------------------------------------------- optional fields, when set
    Write-Section 'Optional properties are sent when, and only when, they are supplied'
    $thumbprintSpec = @(
        @{
            Fqdn          = 'esx-12.vcf.lab'
            Username      = 'root'
            Password      = 'VMw@re1!VMw@re1!'
            StorageType   = 'VSAN'
            NetworkPoolId = '2e8c8e3f-1c8a-4f2b-8a9d-0f3c9f0a1234'
            NetworkPoolName = ''
            SshThumbprint = '11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44'
            SslThumbprint = 'AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD'
            VvolStorageProtocolType = 'NFS'
        }
    )
    $optionalMock = Start-Mock -Scenario 'PrecheckPasses' -Tag 'optional'
    try {
        $null = Invoke-VcfHostCommissionWorkflow -BaseUrl $optionalMock.BaseUrl -Username 'administrator@vsphere.local' `
            -Password 'VMw@re1!VMw@re1!' -HostSpec $thumbprintSpec
    }
    finally { Stop-Mock $optionalMock }

    $optionalLog = Read-RequestLog -Path $optionalMock.LogPath
    $optionalValidate = @($optionalLog) | Where-Object { $_.method -eq 'POST' -and $_.path -eq '/v1/hosts/validations' } | Select-Object -First 1
    Assert-True -Condition ($null -ne $optionalValidate) -Message 'the precheck request was recorded'
    if ($optionalValidate) {
        $optionalBody = @([string]$optionalValidate.body | ConvertFrom-Json)[0]
        Assert-SetEqual -Expected @('fqdn', 'username', 'password', 'storageType', 'networkPoolId', 'networkPoolName', 'sshThumbprint', 'sslThumbprint', 'vvolStorageProtocolType') `
            -Actual (Get-PropertyNames -Object $optionalBody) -Message 'all supplied optional properties are sent alongside the required ones'
        Assert-Equal -Expected '' -Actual ([string]$optionalBody.networkPoolName) `
            -Message 'a supplied empty optional value is preserved rather than treated as an omitted key'
        Assert-Equal -Expected '11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44' -Actual ([string]$optionalBody.sshThumbprint) -Message 'sshThumbprint value'
        Assert-Equal -Expected 'AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD' -Actual ([string]$optionalBody.sslThumbprint) -Message 'sslThumbprint value'
        Assert-Equal -Expected 'NFS' -Actual ([string]$optionalBody.vvolStorageProtocolType) -Message 'vvolStorageProtocolType value'
    }
}
catch {
    Write-Host "`nUNEXPECTED ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace
    $script:Failures.Add("unexpected error: $($_.Exception.Message)")
}
finally {
    foreach ($process in $script:MockProcesses) {
        if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    }
    Remove-Item -LiteralPath $WorkDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ''
if ($script:Failures.Count -eq 0) {
    Write-Host "VERIFICATION PASSED -- $($script:Checks) checks" -ForegroundColor Green
    exit 0
}

Write-Host "VERIFICATION FAILED -- $($script:Failures.Count) failure(s) after $($script:Checks) checks:" -ForegroundColor Red
foreach ($failure in $script:Failures) { Write-Host "  - $failure" -ForegroundColor Red }
exit 1
