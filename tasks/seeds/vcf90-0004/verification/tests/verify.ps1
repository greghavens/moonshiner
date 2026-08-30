#Requires -Version 7.0

# Protected acceptance harness. It connects the genuine VMware.Sdk.Vcf.SddcManager
# PowerCLI module to a contract-pinned loopback SDDC Manager, drives the module
# under test, and then checks the exact bytes that reached the wire. No live
# VMware endpoint is contacted.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$WarningPreference = 'SilentlyContinue'

$TaskRoot = Split-Path -Parent $PSScriptRoot
$ModulePath = Join-Path $TaskRoot 'VcfNetworkPoolProvisioning.psm1'
$ContractPath = Join-Path $TaskRoot 'docs/contract.json'
$SourcesPath = Join-Path $TaskRoot 'docs/official_sources.json'
$MockPath = Join-Path $PSScriptRoot 'mock_sddc_manager.py'

$PinnedTag = '9.0.0.0'
$PinnedCommit = '85151f6b1bb58f13b6ac0304bfec53904bea085f'
$SpecPath = 'specifications/sddc-manager/sddc-manager-openapi.json'
$Repository = 'https://github.com/vmware/vcf-api-specs'
$RequiredSdkVersion = [version]'13.5.0.25380678'

$FixtureUser = 'svc-vcf-netpool@vsphere.local'
$FixturePassword = 'dummy-vcf-login-pass-90'
$AccessToken = 'dummy-vcf-access-token-90'

$ExistingPool = 'np-mgmt-a'
$ConflictingPool = 'np-mgmt-b'
$NewPool = 'np-vi-c'
$FlakyPool = 'np-vi-d'
$TransientPool = 'np-vi-e'
$ExhaustedPool = 'np-vi-f'
$NonRetryPool = 'np-vi-g'
$DefaultSleepPool = 'np-vi-h'

# ------------------------------------------------------------------ assertions

function Assert-True {
    param([Parameter(Mandatory)][bool]$Condition, [Parameter(Mandatory)][string]$Message)
    if (-not $Condition) { throw "Verification failed: $Message" }
}

function Assert-Equal {
    param([AllowNull()][object]$Actual, [AllowNull()][object]$Expected, [Parameter(Mandatory)][string]$Message)
    if ($null -eq $Actual -and $null -eq $Expected) { return }
    if ($null -eq $Actual -or $null -eq $Expected -or "$Actual" -cne "$Expected") {
        throw "Verification failed: $Message. Expected '$Expected', got '$Actual'."
    }
}

function Assert-MemberSet {
    param([Parameter(Mandatory)][object]$Object, [Parameter(Mandatory)][string[]]$Names, [Parameter(Mandatory)][string]$Message)
    $actual = @($Object.PSObject.Properties.Name | Sort-Object)
    Assert-Equal ($actual -join ',') (@($Names | Sort-Object) -join ',') $Message
}

function Assert-PropertyOrder {
    param([Parameter(Mandatory)][object]$Object, [Parameter(Mandatory)][string[]]$Names, [Parameter(Mandatory)][string]$Message)
    Assert-Equal (@($Object.PSObject.Properties.Name) -join ',') ($Names -join ',') $Message
}

# ------------------------------------------------------- static contract checks

foreach ($required in @($ModulePath, $ContractPath, $SourcesPath, $MockPath)) {
    Assert-True (Test-Path -LiteralPath $required -PathType Leaf) "Missing required file $required"
}

$vendored = @(Get-ChildItem -LiteralPath $TaskRoot -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
        $_.Extension -in @('.dll', '.nupkg') -or $_.Name -match '^VMware\..*\.ps[dm]1$'
    })
Assert-Equal $vendored.Count 0 'VMware SDK binaries or modules must not be vendored into the task'

$source = Get-Content -LiteralPath $ModulePath -Raw
$tokens = $null
$parseErrors = $null
$moduleAst = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$parseErrors)
Assert-Equal $parseErrors.Count 0 'The implementation must be valid PowerShell'
$startSleepCalls = @($moduleAst.FindAll({
            param($node)
            $node -is [System.Management.Automation.Language.CommandAst] -and $node.GetCommandName() -ceq 'Start-Sleep'
        }, $true))
Assert-True ($startSleepCalls.Count -gt 0) 'Retries without SleepAction must use Start-Sleep'
foreach ($pattern in @(
        '(?i)\bInvoke-RestMethod\b', '(?i)\bInvoke-WebRequest\b', '(?i)\bSystem\.Net\.Http\b',
        '(?i)\bHttpClient\b', '(?i)\bWebClient\b', '(?i)\bTcpClient\b', '(?im)^\s*(curl|wget)\b')) {
    Assert-True (-not [regex]::IsMatch($source, $pattern)) "Raw transport is forbidden ($pattern); drive the SDK instead"
}
foreach ($command in @(
        'Initialize-VcfIpPool', 'Initialize-VcfNetwork', 'Initialize-VcfNetworkPool',
        'Invoke-VcfGetNetworkPool', 'Invoke-VcfCreateNetworkPool')) {
    Assert-True ($source -cmatch "(?m)\b$([regex]::Escape($command))\b") "The implementation must use $command"
}

$contract = Get-Content -LiteralPath $ContractPath -Raw | ConvertFrom-Json -Depth 100
$sources = Get-Content -LiteralPath $SourcesPath -Raw | ConvertFrom-Json -Depth 100

$expectedOperations = @(
    [pscustomobject]@{ operationId = 'createToken'; method = 'POST'; path = '/v1/tokens' }
    [pscustomobject]@{ operationId = 'getNetworkPool'; method = 'GET'; path = '/v1/network-pools' }
    [pscustomobject]@{ operationId = 'createNetworkPool'; method = 'POST'; path = '/v1/network-pools' }
)

Assert-Equal $contract.derived_from.repository $Repository 'Contract repository provenance changed'
Assert-Equal $contract.derived_from.repository_tag $PinnedTag 'Contract must be pinned to the 9.0.0.0 tag'
Assert-Equal $contract.derived_from.repository_commit_sha $PinnedCommit 'Contract commit provenance changed'
Assert-Equal $contract.derived_from.spec_path $SpecPath 'Contract must be derived from the SDDC Manager specification'
Assert-Equal $contract.derived_from.license 'Apache-2.0' 'Contract license changed'
Assert-Equal $contract.derived_from.spec_version $PinnedTag 'Contract must be the 9.0.0.0 revision, not 9.1.0.0'
Assert-Equal $sources.repository_commit_sha $PinnedCommit 'Official sources commit provenance changed'
Assert-Equal $sources.spec_path $SpecPath 'Official sources spec path changed'
Assert-Equal $sources.spec_version $PinnedTag 'Official sources must record the 9.0.0.0 specification'
Assert-Equal $sources.license 'Apache-2.0' 'Official sources license changed'

Assert-Equal @($contract.operations).Count $expectedOperations.Count 'Contract must name exactly the operations in scope'
Assert-Equal @($sources.operations).Count $expectedOperations.Count 'Official sources must record exactly the operations in scope'
for ($i = 0; $i -lt $expectedOperations.Count; $i++) {
    $expected = $expectedOperations[$i]
    $actual = @($contract.operations)[$i]
    $record = @($sources.operations)[$i]
    Assert-Equal $actual.operationId $expected.operationId "Contract operationId at index $i"
    Assert-Equal $actual.method $expected.method "Contract method for $($expected.operationId)"
    Assert-Equal $actual.path $expected.path "Contract path for $($expected.operationId)"
    Assert-Equal $record.operationId $expected.operationId "Official source operationId at index $i"
    Assert-Equal $record.method $expected.method "Official source method for $($expected.operationId)"
    Assert-Equal $record.path $expected.path "Official source path for $($expected.operationId)"
    Assert-Equal $record.repository_commit_sha $PinnedCommit "Official source commit for $($expected.operationId)"
    Assert-Equal $record.spec_path $SpecPath "Official source spec path for $($expected.operationId)"
    Assert-Equal $record.source_url "$Repository/blob/$PinnedCommit/$SpecPath" "Official source URL for $($expected.operationId)"
}

# The 9.1.0.0 revision of this same file relaxes Network.required to
# mtu/type/vlanId and adds ipAddressVersion, ipAddressAssignmentMode,
# freeIpCount and usedIpCount. Holding the 9.0.0.0 shape here is what keeps the
# contract on the pinned tag.
Assert-MemberSet $contract.schemas.Network.properties @(
    'id', 'type', 'vlanId', 'mtu', 'subnet', 'mask', 'gateway', 'ipPools', 'freeIps', 'usedIps'
) 'Network properties must match the 9.0.0.0 schema'
Assert-Equal (@($contract.schemas.Network.required | Sort-Object) -join ',') 'gateway,mask,mtu,subnet,type,vlanId' 'Network required fields must match the 9.0.0.0 schema'
Assert-MemberSet $contract.schemas.NetworkPool.properties @('id', 'name', 'networks', 'hostsCount') 'NetworkPool properties changed'
Assert-Equal (@($contract.schemas.NetworkPool.required | Sort-Object) -join ',') 'name,networks' 'NetworkPool required fields changed'
Assert-MemberSet $contract.schemas.IpPool.properties @('start', 'end') 'IpPool properties changed'
Assert-MemberSet $contract.schemas.TokenCreationSpec.properties @('username', 'password', 'apiKey', 'idToken') 'TokenCreationSpec properties changed'

$sdk = @(Get-Module -ListAvailable -Name VMware.Sdk.Vcf.SddcManager |
        Where-Object { $_.Version -eq $RequiredSdkVersion })
Assert-True ($sdk.Count -gt 0) "VMware.Sdk.Vcf.SddcManager $RequiredSdkVersion was not provided by the environment"
Import-Module -ModuleInfo $sdk[0] -Force

Import-Module -Name $ModulePath -Force
$exported = @(Get-Command -Module VcfNetworkPoolProvisioning -CommandType Function | Select-Object -ExpandProperty Name | Sort-Object)
Assert-Equal ($exported -join ',') 'Invoke-VcfNetworkPoolProvision' 'The module must export exactly Invoke-VcfNetworkPoolProvision'
$command = Get-Command Invoke-VcfNetworkPoolProvision
foreach ($parameter in @('Server', 'Name', 'Network', 'MaxAttempts', 'RetryDelaySeconds', 'SleepAction')) {
    Assert-True $command.Parameters.ContainsKey($parameter) "Invoke-VcfNetworkPoolProvision is missing parameter $parameter"
}

# ------------------------------------------------------------------ live drive

$runDirectory = Join-Path ([IO.Path]::GetTempPath()) ("vcf90-0004-" + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $runDirectory
$requestLog = Join-Path $runDirectory 'requests.jsonl'
$readyFile = Join-Path $runDirectory 'ready.txt'
$statePath = Join-Path $runDirectory 'pools.json'
$stdout = Join-Path $runDirectory 'mock.stdout'
$stderr = Join-Path $runDirectory 'mock.stderr'

$mock = $null
$script:Slept = @()
$sleepAction = { param($seconds) $script:Slept += $seconds }
$marks = [ordered]@{}
$outcomes = [ordered]@{}
$exhaustedError = $null
$nonRetryError = $null
$conflictErrors = [ordered]@{}

function Read-RequestLog {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return @() }
    return @(Get-Content -LiteralPath $Path |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json -Depth 30 })
}

try {
    $mock = Start-Process -FilePath 'python3' -ArgumentList @(
        $MockPath, '--contract', $ContractPath, '--log', $requestLog,
        '--ready', $readyFile, '--state', $statePath
    ) -PassThru -NoNewWindow -RedirectStandardOutput $stdout -RedirectStandardError $stderr

    $deadline = [datetime]::UtcNow.AddSeconds(15)
    while (-not (Test-Path -LiteralPath $readyFile -PathType Leaf)) {
        if ($mock.HasExited) {
            $detail = if (Test-Path -LiteralPath $stderr) { Get-Content -LiteralPath $stderr -Raw } else { '' }
            throw "Loopback SDDC Manager exited before becoming ready: $detail"
        }
        if ([datetime]::UtcNow -ge $deadline) { throw 'Timed out waiting for the loopback SDDC Manager.' }
        Start-Sleep -Milliseconds 50
    }
    $port = [int](Get-Content -LiteralPath $readyFile -Raw)

    $securePassword = ConvertTo-SecureString $FixturePassword -AsPlainText -Force
    $server = Connect-VcfSddcManagerServer -Server '127.0.0.1' -Port $port -Protocol 'http' `
        -User $FixtureUser -Password $securePassword -NotDefault -IgnoreInvalidCertificate
    Assert-True ($null -ne $server) 'Could not connect the SDK to the loopback SDDC Manager'
    $marks['connect'] = (Read-RequestLog -Path $requestLog).Count

    # A: the pool already exists exactly as requested.
    $existingDefinition = @(
        @{ Type = 'VSAN'; VlanId = 3001; Mtu = 9000; Subnet = '172.20.30.0'; Mask = '255.255.255.0'; Gateway = '172.20.30.1' }
        @{ Type = 'VMOTION'; VlanId = 3002; Mtu = 9000; Subnet = '172.20.31.0'; Mask = '255.255.255.0'; Gateway = '172.20.31.1' }
    )
    $outcomes['existing'] = Invoke-VcfNetworkPoolProvision -Server $server -Name $ExistingPool `
        -Network $existingDefinition -SleepAction $sleepAction
    $marks['existing'] = (Read-RequestLog -Path $requestLog).Count

    # B: a genuinely new pool, one network with IP ranges and one without.
    $newDefinition = @(
        @{ Type = 'VSAN'; VlanId = 3201; Mtu = 9000; Subnet = '172.20.50.0'; Mask = '255.255.255.0'; Gateway = '172.20.50.1'
            IpPools = @(@{ Start = '172.20.50.10'; End = '172.20.50.60' }) }
        @{ Type = 'VMOTION'; VlanId = 3202; Mtu = 1500; Subnet = '172.20.51.0'; Mask = '255.255.255.0'; Gateway = '172.20.51.1'; IpPools = @() }
    )
    $outcomes['created'] = Invoke-VcfNetworkPoolProvision -Server $server -Name $NewPool `
        -Network $newDefinition -SleepAction $sleepAction
    $marks['created'] = (Read-RequestLog -Path $requestLog).Count

    # C: re-running the very same provisioning must not create a second pool.
    $outcomes['repeat'] = Invoke-VcfNetworkPoolProvision -Server $server -Name $NewPool `
        -Network $newDefinition -SleepAction $sleepAction
    $marks['repeat'] = (Read-RequestLog -Path $requestLog).Count

    # D: the create is accepted but its response is lost. The retry must adopt
    # the pool the lost attempt created instead of creating another one.
    $flakyDefinition = @(
        @{ Type = 'VSAN'; VlanId = 3301; Mtu = 9000; Subnet = '172.20.60.0'; Mask = '255.255.255.0'; Gateway = '172.20.60.1' }
    )
    $outcomes['recovered'] = Invoke-VcfNetworkPoolProvision -Server $server -Name $FlakyPool `
        -Network $flakyDefinition -MaxAttempts 3 -RetryDelaySeconds 7 -SleepAction $sleepAction
    $marks['recovered'] = (Read-RequestLog -Path $requestLog).Count

    # E: 429, 503 and 504 are retried. These failures happen before the server
    # creates anything, so the fourth attempt creates the pool normally.
    $transientDefinition = @(
        @{ Type = 'VSAN'; VlanId = 3401; Mtu = 9000; Subnet = '172.20.70.0'; Mask = '255.255.255.0'; Gateway = '172.20.70.1' }
    )
    $outcomes['transient'] = Invoke-VcfNetworkPoolProvision -Server $server -Name $TransientPool `
        -Network $transientDefinition -MaxAttempts 4 -RetryDelaySeconds 3 -SleepAction $sleepAction
    $marks['transient'] = (Read-RequestLog -Path $requestLog).Count

    # F: MaxAttempts counts total provisioning attempts, not retries after the
    # first attempt. A persistent 503 must stop after exactly two attempts.
    $exhaustedDefinition = @(
        @{ Type = 'VSAN'; VlanId = 3451; Mtu = 9000; Subnet = '172.20.75.0'; Mask = '255.255.255.0'; Gateway = '172.20.75.1' }
    )
    try {
        $null = Invoke-VcfNetworkPoolProvision -Server $server -Name $ExhaustedPool `
            -Network $exhaustedDefinition -MaxAttempts 2 -RetryDelaySeconds 5 -SleepAction $sleepAction
    }
    catch {
        $exhaustedError = $_
    }
    $marks['exhausted'] = (Read-RequestLog -Path $requestLog).Count

    # G: a status outside the retry allow-list is terminating after one attempt.
    $nonRetryDefinition = @(
        @{ Type = 'VSAN'; VlanId = 3501; Mtu = 9000; Subnet = '172.20.80.0'; Mask = '255.255.255.0'; Gateway = '172.20.80.1' }
    )
    try {
        $null = Invoke-VcfNetworkPoolProvision -Server $server -Name $NonRetryPool `
            -Network $nonRetryDefinition -MaxAttempts 3 -RetryDelaySeconds 11 -SleepAction $sleepAction
    }
    catch {
        $nonRetryError = $_
    }
    $marks['nonRetry'] = (Read-RequestLog -Path $requestLog).Count

    # H: without SleepAction, the module must use Start-Sleep itself. A zero
    # delay exercises that path with the genuine cmdlet and no timing dependency.
    $defaultSleepDefinition = @(
        @{ Type = 'VSAN'; VlanId = 3601; Mtu = 9000; Subnet = '172.20.90.0'; Mask = '255.255.255.0'; Gateway = '172.20.90.1' }
    )
    $outcomes['defaultSleep'] = Invoke-VcfNetworkPoolProvision -Server $server -Name $DefaultSleepPool `
        -Network $defaultSleepDefinition -MaxAttempts 2 -RetryDelaySeconds 0
    $marks['defaultSleep'] = (Read-RequestLog -Path $requestLog).Count

    # I: every required network member, including the exact network type, is
    # part of conflict detection. Allocated ipPools remain intentionally ignored.
    $conflictDefinitions = [ordered]@{
        type = @(@{ Type = 'VMOTION'; VlanId = 3101; Mtu = 9000; Subnet = '172.20.40.0'; Mask = '255.255.255.0'; Gateway = '172.20.40.1' })
        vlanId = @(@{ Type = 'VSAN'; VlanId = 3199; Mtu = 9000; Subnet = '172.20.40.0'; Mask = '255.255.255.0'; Gateway = '172.20.40.1' })
        mtu = @(@{ Type = 'VSAN'; VlanId = 3101; Mtu = 1500; Subnet = '172.20.40.0'; Mask = '255.255.255.0'; Gateway = '172.20.40.1' })
        subnet = @(@{ Type = 'VSAN'; VlanId = 3101; Mtu = 9000; Subnet = '172.20.41.0'; Mask = '255.255.255.0'; Gateway = '172.20.40.1' })
        mask = @(@{ Type = 'VSAN'; VlanId = 3101; Mtu = 9000; Subnet = '172.20.40.0'; Mask = '255.255.0.0'; Gateway = '172.20.40.1' })
        gateway = @(@{ Type = 'VSAN'; VlanId = 3101; Mtu = 9000; Subnet = '172.20.40.0'; Mask = '255.255.255.0'; Gateway = '172.20.40.254' })
        networkSet = @(
            @{ Type = 'VSAN'; VlanId = 3101; Mtu = 9000; Subnet = '172.20.40.0'; Mask = '255.255.255.0'; Gateway = '172.20.40.1' }
            @{ Type = 'VMOTION'; VlanId = 3102; Mtu = 9000; Subnet = '172.20.42.0'; Mask = '255.255.255.0'; Gateway = '172.20.42.1' }
        )
    }
    foreach ($field in $conflictDefinitions.Keys) {
        try {
            $null = Invoke-VcfNetworkPoolProvision -Server $server -Name $ConflictingPool `
                -Network $conflictDefinitions[$field] -SleepAction $sleepAction
        }
        catch {
            $conflictErrors[$field] = $_
        }
    }
    $marks['conflict'] = (Read-RequestLog -Path $requestLog).Count
}
finally {
    if ($null -ne $mock -and -not $mock.HasExited) {
        Stop-Process -Id $mock.Id -Force
        $mock.WaitForExit()
    }
}

# ------------------------------------------------------------------- assertions

try {
    $requests = Read-RequestLog -Path $requestLog
    Assert-True ($requests.Count -gt 0) 'The loopback SDDC Manager recorded no requests'

    $handshakes = @($requests | Where-Object { $_.handshake })
    Assert-Equal $handshakes.Count 1 'The SDK session handshake must happen exactly once'
    Assert-True ($handshakes[0].sequence -le $marks['connect']) 'The handshake must belong to the harness connection'

    foreach ($request in $requests) {
        if ($request.handshake) { continue }
        Assert-True ($null -ne $request.operationId) "$($request.method) $($request.path) is outside the contract"
    }

    $slice = {
        param([string]$From, [string]$To)
        $low = if ($From) { $marks[$From] } else { 0 }
        @($requests | Where-Object { $_.sequence -gt $low -and $_.sequence -le $marks[$To] -and -not $_.handshake })
    }

    # --- A: already provisioned. Read only, no create at all.
    $result = $outcomes['existing']
    Assert-PropertyOrder $result @('Name', 'PoolId', 'Outcome', 'Attempts', 'Pool') 'Result property order is incorrect'
    Assert-Equal $result.Name $ExistingPool 'Result name for the already-provisioned pool'
    Assert-Equal $result.Outcome 'AlreadyExists' 'An unchanged existing pool must report AlreadyExists'
    Assert-Equal $result.Attempts 1 'The already-provisioned pool must settle on the first attempt'
    Assert-Equal $result.PoolId '11111111-1111-4111-8111-111111111111' 'The existing pool id must be returned'
    foreach ($case in $outcomes.Keys) {
        Assert-Equal $outcomes[$case].Pool.GetType().FullName 'VMware.Bindings.Vcf.SddcManager.Model.NetworkPool' "The $case result must return the SDK network pool model"
    }
    $calls = & $slice 'connect' 'existing'
    Assert-Equal (@($calls.operationId) -join ',') 'getNetworkPool' 'An existing pool must be adopted with a read and nothing else'

    # --- B: created once.
    $result = $outcomes['created']
    Assert-PropertyOrder $result @('Name', 'PoolId', 'Outcome', 'Attempts', 'Pool') 'Created result property order is incorrect'
    Assert-Equal $result.Outcome 'Created' 'A missing pool must report Created'
    Assert-Equal $result.Attempts 1 'A clean create must settle on the first attempt'
    Assert-True (-not [string]::IsNullOrWhiteSpace($result.PoolId)) 'A created pool must report its id'
    $calls = & $slice 'existing' 'created'
    Assert-Equal (@($calls.operationId) -join ',') 'getNetworkPool,createNetworkPool' 'A create must be preceded by the existence read'

    $create = $calls[1]
    Assert-Equal $create.method 'POST' 'createNetworkPool method'
    Assert-Equal $create.path '/v1/network-pools' 'createNetworkPool path'
    Assert-Equal $create.query '' 'createNetworkPool must not send a query string'
    Assert-Equal $create.headers.authorization "Bearer $AccessToken" 'createNetworkPool must carry the bearer token'
    Assert-True ($create.headers.'content-type' -match '^application/json(?:\s*;.*)?$') 'createNetworkPool content type'
    Assert-MemberSet $create.json @('name', 'networks') 'The create body must carry only name and networks; id and hostsCount are server state'
    Assert-Equal $create.json.name $NewPool 'Created pool name on the wire'

    $networks = @($create.json.networks)
    Assert-Equal $networks.Count 2 'Both requested networks must be sent'

    # The network that was given IP ranges carries ipPools.
    Assert-MemberSet $networks[0] @('type', 'vlanId', 'mtu', 'subnet', 'mask', 'gateway', 'ipPools') 'First network member set'
    Assert-Equal $networks[0].type 'VSAN' 'First network type'
    Assert-Equal $networks[0].vlanId 3201 'First network vlanId'
    Assert-Equal $networks[0].mtu 9000 'First network mtu'
    Assert-Equal $networks[0].subnet '172.20.50.0' 'First network subnet'
    Assert-Equal $networks[0].mask '255.255.255.0' 'First network mask'
    Assert-Equal $networks[0].gateway '172.20.50.1' 'First network gateway'
    $ranges = @($networks[0].ipPools)
    Assert-Equal $ranges.Count 1 'First network ipPools count'
    Assert-MemberSet $ranges[0] @('start', 'end') 'IpPool member set'
    Assert-Equal $ranges[0].start '172.20.50.10' 'IpPool start'
    Assert-Equal $ranges[0].end '172.20.50.60' 'IpPool end'

    # Even when the caller explicitly supplies an empty range array, the
    # network with no ranges must omit ipPools rather than send it empty.
    Assert-MemberSet $networks[1] @('type', 'vlanId', 'mtu', 'subnet', 'mask', 'gateway') 'A network without IP ranges must omit ipPools instead of sending null or []'
    Assert-Equal $networks[1].type 'VMOTION' 'Second network type'
    Assert-Equal $networks[1].vlanId 3202 'Second network vlanId'
    Assert-Equal $networks[1].mtu 1500 'Second network mtu'
    Assert-Equal $networks[1].subnet '172.20.51.0' 'Second network subnet'
    Assert-Equal $networks[1].mask '255.255.255.0' 'Second network mask'
    Assert-Equal $networks[1].gateway '172.20.51.1' 'Second network gateway'

    # --- C: the identical run again is a read, never a second create.
    $result = $outcomes['repeat']
    Assert-PropertyOrder $result @('Name', 'PoolId', 'Outcome', 'Attempts', 'Pool') 'Repeated result property order is incorrect'
    Assert-Equal $result.Outcome 'AlreadyExists' 'Repeating an identical provisioning must report AlreadyExists'
    Assert-Equal $result.Attempts 1 'The repeated run must settle on the first attempt'
    Assert-Equal $result.PoolId $outcomes['created'].PoolId 'The repeated run must return the pool the first run created'
    $calls = & $slice 'created' 'repeat'
    Assert-Equal (@($calls.operationId) -join ',') 'getNetworkPool' 'Repeating an identical provisioning must not create anything'

    # --- D: create accepted, response lost, retry adopts the orphan.
    $result = $outcomes['recovered']
    Assert-PropertyOrder $result @('Name', 'PoolId', 'Outcome', 'Attempts', 'Pool') 'Recovered result property order is incorrect'
    Assert-Equal $result.Outcome 'RecoveredAfterRetry' 'A create whose response was lost must be recovered, not repeated'
    Assert-Equal $result.Attempts 2 'Recovery must be reported as the second attempt'
    Assert-True (-not [string]::IsNullOrWhiteSpace($result.PoolId)) 'The recovered pool must report its id'
    $calls = & $slice 'repeat' 'recovered'
    Assert-Equal (@($calls.operationId) -join ',') 'getNetworkPool,createNetworkPool,getNetworkPool' 'A lost response must be resolved by re-reading, not by creating again'
    Assert-MemberSet $calls[1].json @('name', 'networks') 'Recovered create body member set'
    Assert-Equal $calls[1].json.name $FlakyPool 'Recovered create pool name'
    Assert-MemberSet @($calls[1].json.networks)[0] @('type', 'vlanId', 'mtu', 'subnet', 'mask', 'gateway') 'The recovered create must omit ipPools'
    Assert-Equal (@($script:Slept) -join ',') '7,3,3,3,5' 'Retries must back off using RetryDelaySeconds'

    # --- E: all four retryable statuses, and a read before every attempt.
    $result = $outcomes['transient']
    Assert-PropertyOrder $result @('Name', 'PoolId', 'Outcome', 'Attempts', 'Pool') 'Retried result property order is incorrect'
    Assert-Equal $result.Outcome 'Created' 'A create that eventually succeeds must report Created'
    Assert-Equal $result.Attempts 4 '429, 503 and 504 must each consume one provisioning attempt'
    $calls = & $slice 'recovered' 'transient'
    Assert-Equal (@($calls.operationId) -join ',') 'getNetworkPool,createNetworkPool,getNetworkPool,createNetworkPool,getNetworkPool,createNetworkPool,getNetworkPool,createNetworkPool' 'Every transient create retry must be preceded by an existence read'

    # --- F: total attempt limit.
    Assert-True ($null -ne $exhaustedError) 'A retryable failure must terminate when MaxAttempts is exhausted'
    $calls = & $slice 'transient' 'exhausted'
    Assert-Equal (@($calls.operationId) -join ',') 'getNetworkPool,createNetworkPool,getNetworkPool,createNetworkPool' 'MaxAttempts must count total attempts and each retry must begin with a read'

    # --- G: non-retryable status.
    Assert-True ($null -ne $nonRetryError) 'A non-retryable create failure must be terminating'
    $calls = & $slice 'exhausted' 'nonRetry'
    Assert-Equal (@($calls.operationId) -join ',') 'getNetworkPool,createNetworkPool' 'A non-retryable failure must not make another provisioning attempt'

    # --- H: default sleep implementation.
    $result = $outcomes['defaultSleep']
    Assert-PropertyOrder $result @('Name', 'PoolId', 'Outcome', 'Attempts', 'Pool') 'Default-sleep result property order is incorrect'
    Assert-Equal $result.Outcome 'Created' 'A create after the default sleep must report Created'
    Assert-Equal $result.Attempts 2 'The default-sleep scenario must settle on the second attempt'
    $calls = & $slice 'nonRetry' 'defaultSleep'
    Assert-Equal (@($calls.operationId) -join ',') 'getNetworkPool,createNetworkPool,getNetworkPool,createNetworkPool' 'The retry after Start-Sleep must begin with an existence read'

    # --- I: name taken by a different definition.
    foreach ($field in $conflictDefinitions.Keys) {
        Assert-True ($conflictErrors.Contains($field)) "A difference in $field must be a terminating conflict"
        Assert-True ($conflictErrors[$field].Exception.Message -match [regex]::Escape($ConflictingPool)) "The $field conflict error must name the pool"
    }
    $calls = & $slice 'defaultSleep' 'conflict'
    Assert-Equal $calls.Count $conflictDefinitions.Count 'Every conflicting definition must perform exactly one existence read'
    Assert-True (@($calls | Where-Object { $_.operationId -cne 'getNetworkPool' }).Count -eq 0) 'A conflicting definition must not reach createNetworkPool'

    # --- whole run: creates, auth and bodies.
    $creates = @($requests | Where-Object { $_.operationId -eq 'createNetworkPool' })
    Assert-Equal $creates.Count 11 'Unexpected number of createNetworkPool requests across retry scenarios'
    Assert-Equal (@($creates | ForEach-Object { $_.json.name }) -join ',') "$NewPool,$FlakyPool,$TransientPool,$TransientPool,$TransientPool,$TransientPool,$ExhaustedPool,$ExhaustedPool,$NonRetryPool,$DefaultSleepPool,$DefaultSleepPool" 'Unexpected create request order or retry target'

    $tokens = @($requests | Where-Object { $_.operationId -eq 'createToken' })
    Assert-Equal $tokens.Count 1 'The session token must be created once'
    Assert-MemberSet $tokens[0].json @('username', 'password') 'Unset token alternatives must be omitted from the token request'
    Assert-Equal $tokens[0].json.username $FixtureUser 'Token username on the wire'
    Assert-True ([string]::IsNullOrEmpty($tokens[0].headers.authorization)) 'Token creation must not send an Authorization header'

    foreach ($request in $requests) {
        if ($request.handshake -or $request.operationId -eq 'createToken') { continue }
        Assert-Equal $request.headers.authorization "Bearer $AccessToken" "$($request.operationId) must carry the bearer token"
        if ($request.method -eq 'GET') {
            Assert-Equal $request.bodyText '' "$($request.operationId) must not send a request body"
            Assert-Equal $request.query '' "$($request.operationId) must not send a query string"
        }
    }

    # --- surviving server state.
    $pools = @(Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json -Depth 100)
    Assert-Equal $pools.Count 6 'The loopback SDDC Manager must hold exactly six pools when the run ends'
    $names = @($pools | ForEach-Object { $_.name })
    Assert-Equal (@($names | Sort-Object) -join ',') "$ExistingPool,$ConflictingPool,$NewPool,$FlakyPool,$TransientPool,$DefaultSleepPool" 'Unexpected pools survived the run'
    foreach ($name in $names) {
        Assert-Equal @($names | Where-Object { $_ -ceq $name }).Count 1 "Pool '$name' was created more than once"
    }

    Write-Output 'PASS: network pool provisioning is retry-safe and the createNetworkPool wire shape matches the 9.0.0.0 contract.'
}
finally {
    Remove-Module VcfNetworkPoolProvisioning -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $runDirectory) {
        Remove-Item -LiteralPath $runDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
}
