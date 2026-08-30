# Protected acceptance harness for VcfHostCommission.psm1.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture =
    [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture =
    [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'
Set-Location -LiteralPath $PSScriptRoot

$script:Checks = 0
$script:Failures = 0

function Assert-True {
    param([string] $Label, [bool] $Condition)
    $script:Checks++
    if ($Condition) { return }
    $script:Failures++
    Write-Output "FAIL $Label"
}

function Assert-Eq {
    param([string] $Label, $Expected, $Actual)
    $script:Checks++
    if ("$Expected" -ceq "$Actual") { return }
    $script:Failures++
    Write-Output "FAIL $Label"
    Write-Output "  expected: $Expected"
    Write-Output "  actual:   $Actual"
}

function Get-RequestLog {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return @() }
    @(
        Get-Content -LiteralPath $Path |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
}

function Get-JsonPropertyNames {
    param([Parameter(Mandatory)] [object] $InputObject)
    @($InputObject.PSObject.Properties.Name | Sort-Object)
}

$modulePath = Join-Path $PSScriptRoot 'VcfHostCommission.psm1'
if (-not (Test-Path -LiteralPath $modulePath -PathType Leaf)) {
    Write-Output 'FAIL VcfHostCommission.psm1 not found in workspace root'
    exit 1
}

# PowerCLI is an environment prerequisite, never a fixture supplied by this seed.
$sdk = Get-Module -ListAvailable -Name 'VMware.Sdk.Vcf.SddcManager' |
    Where-Object { $_.Version -ge [version] '13.5.0.25380678' } |
    Sort-Object Version -Descending |
    Select-Object -First 1
if ($null -eq $sdk) {
    Write-Output (
        'FAIL prerequisite VMware.Sdk.Vcf.SddcManager ' +
        '>= 13.5.0.25380678 is not installed'
    )
    exit 1
}

$source = Get-Content -LiteralPath $modulePath -Raw
foreach ($forbidden in @(
    '\bInvoke-WebRequest\b',
    '\bInvoke-RestMethod\b',
    '\bSystem\.Net\.Http\b',
    '\bHttpClient\b',
    '\bWebClient\b',
    '\bTcpClient\b',
    '\bcurl\b',
    '\bwget\b'
)) {
    Assert-True "solution does not bypass the VMware SDK with $forbidden" (
        $source -notmatch $forbidden
    )
}
Assert-True 'solution imports the VMware SDK module' (
    $source -match '\bVMware\.Sdk\.Vcf\.SddcManager\b'
)
Assert-True 'solution constructs the SDK HostCommissionSpec' (
    $source -match '\bInitialize-VcfHostCommissionSpec\b'
)
Assert-True 'solution calls Invoke-VcfCommissionHosts' (
    $source -match '\bInvoke-VcfCommissionHosts\b'
)
Assert-True 'solution calls Invoke-VcfGetTask' (
    $source -match '\bInvoke-VcfGetTask\b'
)

$vendored = @(
    Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File |
        Where-Object {
            $_.Extension.ToLowerInvariant() -in @(
                '.dll', '.nupkg', '.snupkg', '.zip'
            )
        }
)
Assert-Eq 'solution does not vendor binary dependencies' 0 $vendored.Count

# Verify the protected OpenAPI projection and its per-operation provenance.
$contractPath = Join-Path $PSScriptRoot 'docs/contract.json'
$sourcesPath = Join-Path $PSScriptRoot 'docs/official_sources.json'
$mockPath = Join-Path $PSScriptRoot 'mock_sddc.py'
$expectedProtectedHashes = @{
    $contractPath = '0f5bd3b4f006c617d716bd98a958cc85e907096ed1e7b0a81c62f33ed05d0ff6'
    $sourcesPath = 'a9e22a6a3c0d6363798deb5c80af81ebee874d0adb71880a0d5243e4190167b7'
    $mockPath = '8edddd28a211498c7e1446ac4f390af7224926fd74a32b7833d57c53b1eb584e'
}
foreach ($entry in $expectedProtectedHashes.GetEnumerator()) {
    $actualHash = (Get-FileHash -LiteralPath $entry.Key -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-Eq "protected file hash $([IO.Path]::GetFileName($entry.Key))" `
        $entry.Value $actualHash
}

$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
$sources = Get-Content -LiteralPath $sourcesPath -Raw | ConvertFrom-Json
$expectedSha = '3949fc33339fc5ea1b77eadb258f1cf49aa88e26'
$expectedPath = 'specifications/sddc-manager/sddc-manager-openapi.json'
$expectedOps = 'createToken,commissionHosts,getTask'
Assert-Eq 'contract pins OpenAPI 3.0.1' '3.0.1' $contract.source.openapiVersion
Assert-Eq 'contract pins VCF 9.1' '9.1.0.0' $contract.source.apiVersion
Assert-Eq 'contract commit sha' $expectedSha $contract.source.commitSha
Assert-Eq 'contract spec path' $expectedPath $contract.source.specPath
Assert-Eq 'contract operationIds' $expectedOps (
    ($contract.operations.operationId) -join ','
)
Assert-Eq 'contract operation methods' 'POST,POST,GET' (
    ($contract.operations.method) -join ','
)
Assert-Eq 'contract operation paths' '/v1/tokens,/v1/hosts,/v1/tasks/{id}' (
    ($contract.operations.path) -join ','
)
Assert-Eq 'HostCommissionSpec required fields match source' (
    'fqdn,networkPoolId,password,storageType,username'
) (($contract.schemas.HostCommissionSpec.required) -join ',')
Assert-Eq 'HostCommissionSpec projected property order' (
    'fqdn,username,password,storageType,vvolStorageProtocolType,' +
    'networkPoolId,networkPoolName,sshThumbprint,sslThumbprint'
) (
    ($contract.schemas.HostCommissionSpec.properties.PSObject.Properties.Name) `
        -join ','
)
Assert-Eq 'official source license' 'Apache-2.0' $sources.license
Assert-Eq 'official source commit sha' $expectedSha `
    $sources.specification.repository_commit_sha
Assert-Eq 'official source spec path' $expectedPath `
    $sources.specification.spec_path
Assert-Eq 'official source operationIds' $expectedOps (
    ($sources.operations.operationId) -join ','
)
foreach ($entry in $sources.operations) {
    Assert-Eq "source $($entry.operationId) repeats commit" $expectedSha `
        $entry.repository_commit_sha
    Assert-Eq "source $($entry.operationId) repeats path" $expectedPath `
        $entry.spec_path
}

Import-Module 'VMware.Sdk.Vcf.SddcManager' `
    -MinimumVersion '13.5.0.25380678' `
    -Force
Import-Module $modulePath -Force

$exports = @(
    Get-Command -Module VcfHostCommission -CommandType Function |
        Select-Object -ExpandProperty Name
)
Assert-Eq 'module exports exactly one function' `
    'Start-VcfHostCommissionAndWait' ($exports -join ',')

foreach ($commandName in @(
    'Initialize-VcfHostCommissionSpec',
    'Invoke-VcfCommissionHosts',
    'Invoke-VcfGetTask'
)) {
    $command = Get-Command $commandName -ErrorAction Stop
    Assert-Eq "$commandName comes from genuine SDK" `
        'VMware.Sdk.Vcf.SddcManager' $command.Source
}

$minimalFqdn = 'esx-minimal.lab.example'
$fullFqdn = 'esx-vvol.lab.example'
$timeoutFqdn = 'esx-timeout.lab.example'
$minimalTask = '11111111-1111-4111-8111-111111111111'
$fullTask = '22222222-2222-4222-8222-222222222222'
$timeoutTask = '33333333-3333-4333-8333-333333333333'
$username = 'svc-vcf-commission'
$password = 'dummy-vcf-login-pass-91'
$accessToken = 'dummy-vcf-access-token-91'
$minimalEsxiPassword = 'dummy-esxi-minimal-pass-91'
$fullEsxiPassword = 'dummy-esxi-vvol-pass-91'
$timeoutEsxiPassword = 'dummy-esxi-timeout-pass-91'

$scratch = Join-Path $PSScriptRoot '_verification'
New-Item -ItemType Directory -Force -Path $scratch > $null
$portFile = Join-Path $scratch 'port.txt'
$requestLog = Join-Path $scratch 'requests.jsonl'
$serverOut = Join-Path $scratch 'server.out'
$serverErr = Join-Path $scratch 'server.err'
Remove-Item -LiteralPath $portFile, $requestLog, $serverOut, $serverErr `
    -ErrorAction SilentlyContinue

$serverProcess = $null
try {
    $serverProcess = Start-Process -FilePath 'python3' `
        -ArgumentList @($mockPath, $portFile, $requestLog) `
        -PassThru `
        -RedirectStandardOutput $serverOut `
        -RedirectStandardError $serverErr
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Path -LiteralPath $portFile -PathType Leaf)) {
        if ($serverProcess.HasExited -or [DateTime]::UtcNow -gt $deadline) {
            $detail = Get-Content -LiteralPath $serverErr -Raw `
                -ErrorAction SilentlyContinue
            throw "loopback mock failed to start: $detail"
        }
        Start-Sleep -Milliseconds 40
    }
    $port = [int] (Get-Content -LiteralPath $portFile -Raw).Trim()

    $securePassword = ConvertTo-SecureString $password -AsPlainText -Force
    $loginCredential = [pscredential]::new($username, $securePassword)
    $connection = Connect-VcfSddcManagerServer `
        -Server '127.0.0.1' `
        -Port $port `
        -Protocol 'http' `
        -Credential $loginCredential `
        -NotDefault
    $connection = @($connection)[0]
    Assert-True 'real SDK connected to loopback' ($null -ne $connection)

    # Minimal body: every optional HostCommissionSpec field must be absent.
    $minimalSecure = ConvertTo-SecureString $minimalEsxiPassword `
        -AsPlainText -Force
    $minimalCredential = [pscredential]::new('root', $minimalSecure)
    $script:MinimalSleeps = [System.Collections.Generic.List[int]]::new()
    $result = Start-VcfHostCommissionAndWait `
        -Server $connection `
        -Fqdn $minimalFqdn `
        -Credential $minimalCredential `
        -StorageType VSAN `
        -NetworkPoolId 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' `
        -PollLimit 5 `
        -PollIntervalSeconds 7 `
        -SleepAction {
            param($seconds)
            $script:MinimalSleeps.Add([int] $seconds)
        }
    Assert-Eq 'result key order' 'Fqdn,TaskId,Status,PollCount' (
        ($result.PSObject.Properties.Name) -join ','
    )
    Assert-Eq 'result FQDN' $minimalFqdn $result.Fqdn
    Assert-Eq 'result task id' $minimalTask $result.TaskId
    Assert-Eq 'result preserves display-form final status' `
        'Completed With Warning' $result.Status
    Assert-Eq 'result counts GET polls only' 3 $result.PollCount
    Assert-Eq 'success sleeps only between non-terminal polls' `
        '7,7' ($script:MinimalSleeps -join ',')

    # All optional fields are bound; the task ends in a detailed failure.
    $fullSecure = ConvertTo-SecureString $fullEsxiPassword -AsPlainText -Force
    $fullCredential = [pscredential]::new('root', $fullSecure)
    $script:FailureSleeps = [System.Collections.Generic.List[int]]::new()
    $failure = $null
    try {
        Start-VcfHostCommissionAndWait `
            -Server $connection `
            -Fqdn $fullFqdn `
            -Credential $fullCredential `
            -StorageType VVOL `
            -NetworkPoolId 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' `
            -VvolStorageProtocolType FC `
            -NetworkPoolName 'vvol-host-pool' `
            -SshThumbprint 'SHA256:fixture-ssh-thumbprint' `
            -SslThumbprint 'AA:BB:CC:DD:EE:FF' `
            -PollLimit 5 `
            -PollIntervalSeconds 3 `
            -SleepAction {
                param($seconds)
                $script:FailureSleeps.Add([int] $seconds)
            } > $null
    }
    catch {
        $failure = $_.Exception
    }
    Assert-True 'failed host task throws' ($null -ne $failure)
    Assert-Eq 'failure exception type' `
        'VcfHostCommissionFailedException' $failure.GetType().Name
    Assert-Eq 'failure exception task id' $fullTask $failure.TaskId
    Assert-Eq 'failure exception status' 'Failed' $failure.TaskStatus
    Assert-Eq 'failure exception error code' `
        'HOST_COMMISSION_FAILED' $failure.ErrorCode
    Assert-Eq 'failure exception reference token' `
        'fixture-host-failure-ref' $failure.ReferenceToken
    Assert-True 'failure exception uses first task error message' (
        $failure.Message -like '*host commission workflow failed*'
    )
    foreach ($secret in @(
        $password,
        $minimalEsxiPassword,
        $fullEsxiPassword,
        $timeoutEsxiPassword,
        $accessToken
    )) {
        Assert-True 'failure exception does not reveal credentials or token' (
            $failure.Message -notlike "*$secret*"
        )
    }
    Assert-Eq 'failure sleeps once' '3' ($script:FailureSleeps -join ',')

    # Unknown status remains non-terminal and PollLimit is exact.
    $timeoutSecure = ConvertTo-SecureString $timeoutEsxiPassword `
        -AsPlainText -Force
    $timeoutCredential = [pscredential]::new('root', $timeoutSecure)
    $script:TimeoutSleeps = [System.Collections.Generic.List[int]]::new()
    $timeout = $null
    try {
        Start-VcfHostCommissionAndWait `
            -Server $connection `
            -Fqdn $timeoutFqdn `
            -Credential $timeoutCredential `
            -StorageType NFS `
            -NetworkPoolId 'cccccccc-cccc-4ccc-8ccc-cccccccccccc' `
            -PollLimit 2 `
            -PollIntervalSeconds 11 `
            -SleepAction {
                param($seconds)
                $script:TimeoutSleeps.Add([int] $seconds)
            } > $null
    }
    catch {
        $timeout = $_.Exception
    }
    Assert-True 'poll exhaustion throws' ($null -ne $timeout)
    Assert-Eq 'timeout exception type' `
        'VcfHostCommissionTimeoutException' $timeout.GetType().Name
    Assert-Eq 'timeout exception task id' $timeoutTask $timeout.TaskId
    Assert-Eq 'timeout exception poll count' 2 $timeout.PollCount
    Assert-Eq 'timeout sleeps only between permitted polls' `
        '11' ($script:TimeoutSleeps -join ',')
    Assert-True 'timeout exception does not reveal bearer token' (
        $timeout.Message -notlike "*$accessToken*"
    )

    $log = @(Get-RequestLog -Path $requestLog)
    $expectedSequence = @(
        'createToken',
        $null,
        'commissionHosts',
        'getTask',
        'getTask',
        'getTask',
        'commissionHosts',
        'getTask',
        'getTask',
        'commissionHosts',
        'getTask',
        'getTask'
    )
    Assert-Eq 'exact REST operation sequence' ($expectedSequence -join ',') `
        (($log.operationId) -join ',')
    Assert-Eq 'total wire request count' 12 $log.Count
    Assert-Eq 'mock sees only contract operationIds' (
        'commissionHosts,createToken,getTask'
    ) (
        (
            @(
                $log.operationId |
                    Where-Object { $null -ne $_ } |
                    Sort-Object -Unique
            )
        ) -join ','
    )
    Assert-True 'every request target omits a query string' (
        @($log | Where-Object { $_.rawQuery -ne '' }).Count -eq 0
    )
    Assert-True 'every request remains on the loopback authority' (
        @(
            $log | Where-Object {
                $_.headers.host -cne "127.0.0.1:$port"
            }
        ).Count -eq 0
    )
    Assert-True 'every SDK request accepts JSON responses' (
        @(
            $log | Where-Object {
                $_.headers.accept -notlike '*application/json*'
            }
        ).Count -eq 0
    )

    $tokenRequests = @($log | Where-Object operationId -CEQ 'createToken')
    Assert-Eq 'one SDK token request' 1 $tokenRequests.Count
    Assert-Eq 'token method' 'POST' $tokenRequests[0].method
    Assert-Eq 'token target' '/v1/tokens' $tokenRequests[0].rawTarget
    Assert-Eq 'token response status' 201 $tokenRequests[0].responseStatus
    Assert-True 'token content type is JSON' (
        $tokenRequests[0].contentType -like 'application/json*'
    )
    Assert-Eq 'token request is not bearer-authenticated' '' `
        $tokenRequests[0].authorization
    $tokenBody = $tokenRequests[0].body | ConvertFrom-Json
    Assert-Eq 'token body has only bound credential fields' 'password,username' (
        (Get-JsonPropertyNames $tokenBody) -join ','
    )
    Assert-Eq 'token username' $username $tokenBody.username
    Assert-Eq 'token password' $password $tokenBody.password

    $connectionProbes = @(
        $log | Where-Object {
            $null -eq $_.operationId -and
            $_.path -ceq '/v1/sddc-manager'
        }
    )
    Assert-Eq 'one genuine SDK connection version probe' 1 `
        $connectionProbes.Count
    Assert-Eq 'connection version probe method' 'GET' `
        $connectionProbes[0].method
    Assert-Eq 'connection version probe target' '/v1/sddc-manager' `
        $connectionProbes[0].rawTarget
    Assert-Eq 'connection version probe response status' 200 `
        $connectionProbes[0].responseStatus
    Assert-Eq 'connection version probe has no body' 0 `
        $connectionProbes[0].bodyLength
    Assert-Eq 'connection version probe carries SDK bearer token' `
        "Bearer $accessToken" $connectionProbes[0].authorization

    $commissions = @($log | Where-Object operationId -CEQ 'commissionHosts')
    Assert-Eq 'three host submissions' 3 $commissions.Count
    Assert-Eq 'all submissions are POST' 'POST,POST,POST' (
        ($commissions.method) -join ','
    )
    Assert-Eq 'all submission targets are exact' '/v1/hosts,/v1/hosts,/v1/hosts' (
        ($commissions.rawTarget) -join ','
    )
    Assert-Eq 'all submissions are accepted' '202,202,202' (
        ($commissions.responseStatus) -join ','
    )
    Assert-Eq 'all submissions carry SDK bearer token' (
        "Bearer $accessToken,Bearer $accessToken,Bearer $accessToken"
    ) (($commissions.authorization) -join ',')
    Assert-True 'all submission content types are JSON' (
        @($commissions | Where-Object {
            $_.contentType -notlike 'application/json*'
        }).Count -eq 0
    )

    $minimalArray = @($commissions[0].body | ConvertFrom-Json)
    Assert-Eq 'minimal request is a one-element JSON array' 1 $minimalArray.Count
    $minimalBody = $minimalArray[0]
    Assert-Eq 'minimal body has exact member set' (
        'fqdn,networkPoolId,password,storageType,username'
    ) ((Get-JsonPropertyNames $minimalBody) -join ',')
    Assert-Eq 'minimal body FQDN' $minimalFqdn $minimalBody.fqdn
    Assert-Eq 'minimal body username' 'root' $minimalBody.username
    Assert-Eq 'minimal body password' $minimalEsxiPassword $minimalBody.password
    Assert-Eq 'minimal body storage type' 'VSAN' $minimalBody.storageType
    Assert-Eq 'minimal body network pool id' `
        'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' $minimalBody.networkPoolId
    foreach ($omitted in @(
        'vvolStorageProtocolType',
        'networkPoolName',
        'sshThumbprint',
        'sslThumbprint'
    )) {
        Assert-True "minimal body omits unset optional $omitted" (
            $minimalBody.PSObject.Properties.Name -cnotcontains $omitted
        )
    }

    $fullArray = @($commissions[1].body | ConvertFrom-Json)
    Assert-Eq 'full request is a one-element JSON array' 1 $fullArray.Count
    $fullBody = $fullArray[0]
    Assert-Eq 'full body has exact member set' (
        'fqdn,networkPoolId,networkPoolName,password,sshThumbprint,' +
        'sslThumbprint,storageType,username,vvolStorageProtocolType'
    ) ((Get-JsonPropertyNames $fullBody) -join ',')
    Assert-Eq 'full body FQDN' $fullFqdn $fullBody.fqdn
    Assert-Eq 'full body username' 'root' $fullBody.username
    Assert-Eq 'full body password' $fullEsxiPassword $fullBody.password
    Assert-Eq 'full body storage type' 'VVOL' $fullBody.storageType
    Assert-Eq 'full body vVol protocol' 'FC' $fullBody.vvolStorageProtocolType
    Assert-Eq 'full body network pool id' `
        'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' $fullBody.networkPoolId
    Assert-Eq 'full body network pool name' `
        'vvol-host-pool' $fullBody.networkPoolName
    Assert-Eq 'full body SSH thumbprint' `
        'SHA256:fixture-ssh-thumbprint' $fullBody.sshThumbprint
    Assert-Eq 'full body SSL thumbprint' `
        'AA:BB:CC:DD:EE:FF' $fullBody.sslThumbprint

    $timeoutArray = @($commissions[2].body | ConvertFrom-Json)
    Assert-Eq 'timeout request is a one-element JSON array' 1 $timeoutArray.Count
    $timeoutBody = $timeoutArray[0]
    Assert-Eq 'timeout body also has exact minimal member set' (
        'fqdn,networkPoolId,password,storageType,username'
    ) ((Get-JsonPropertyNames $timeoutBody) -join ',')
    Assert-Eq 'timeout body FQDN' $timeoutFqdn $timeoutBody.fqdn
    foreach ($omitted in @(
        'vvolStorageProtocolType',
        'networkPoolName',
        'sshThumbprint',
        'sslThumbprint'
    )) {
        Assert-True "timeout body omits unset optional $omitted" (
            $timeoutBody.PSObject.Properties.Name -cnotcontains $omitted
        )
    }

    $minimalPolls = @(
        $log | Where-Object {
            $_.operationId -ceq 'getTask' -and
            $_.path -ceq "/v1/tasks/$minimalTask"
        }
    )
    $failurePolls = @(
        $log | Where-Object {
            $_.operationId -ceq 'getTask' -and
            $_.path -ceq "/v1/tasks/$fullTask"
        }
    )
    $timeoutPolls = @(
        $log | Where-Object {
            $_.operationId -ceq 'getTask' -and
            $_.path -ceq "/v1/tasks/$timeoutTask"
        }
    )
    Assert-Eq 'success performs three real task GETs' 3 $minimalPolls.Count
    Assert-Eq 'failure stops after terminal second task GET' 2 `
        $failurePolls.Count
    Assert-Eq 'PollLimit bounds timeout GET count exactly' 2 `
        $timeoutPolls.Count
    $polls = @($minimalPolls + $failurePolls + $timeoutPolls)
    Assert-Eq 'all polls use GET' 'GET,GET,GET,GET,GET,GET,GET' (
        ($polls.method) -join ','
    )
    Assert-Eq 'all polls have zero-length bodies' '0,0,0,0,0,0,0' (
        ($polls.bodyLength) -join ','
    )
    Assert-Eq 'all polls carry SDK bearer token' (
        (
            @(
                "Bearer $accessToken",
                "Bearer $accessToken",
                "Bearer $accessToken",
                "Bearer $accessToken",
                "Bearer $accessToken",
                "Bearer $accessToken",
                "Bearer $accessToken"
            )
        ) -join ','
    ) (($polls.authorization) -join ',')
}
catch {
    $script:Failures++
    Write-Output "FAIL verifier setup or execution: $($_.Exception.Message)"
    if ($_.ScriptStackTrace) { Write-Output $_.ScriptStackTrace }
}
finally {
    if ($null -ne $serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
        $serverProcess.WaitForExit()
    }
}

if ($script:Failures -gt 0) {
    Write-Output "FAILED: $script:Failures failure(s), $script:Checks checks"
    exit 1
}
Write-Output "ALL TESTS PASSED ($script:Checks checks)"
