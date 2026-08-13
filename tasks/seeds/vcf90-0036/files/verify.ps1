#Requires -Version 7.0
<#
    Acceptance harness for VcfLibraryItemRegistration.

    Starts the contract-pinned loopback vCenter fixture on 127.0.0.1, drives the
    module against it with dummy credentials, then reads the fixture's request
    log and checks the exact wire shape of every request the module made. No
    live VMware endpoint is contacted.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$modulePath = Join-Path $root 'VcfLibraryItemRegistration.psm1'
$contractPath = Join-Path $root 'docs/contract.json'
$mockPath = Join-Path $root 'mock_vcenter.py'

$workDir = Join-Path ([System.IO.Path]::GetTempPath()) ("vcf90-0036-" + [Guid]::NewGuid().ToString('n'))
$null = New-Item -ItemType Directory -Path $workDir
$logPath = Join-Path $workDir 'requests.jsonl'
$readyPath = Join-Path $workDir 'ready.json'
$stderrPath = Join-Path $workDir 'fixture.err'

$script:Failures = [System.Collections.Generic.List[string]]::new()
$script:Checks = 0

function Assert-That {
    param([Parameter(Mandatory)][string]$Description, [Parameter(Mandatory)][bool]$Condition, [string]$Detail)
    $script:Checks++
    if (-not $Condition) {
        $line = "FAIL  $Description"
        if ($Detail) { $line += "`n        $Detail" }
        $script:Failures.Add($line)
    }
}

function Assert-Equal {
    param([Parameter(Mandatory)][string]$Description, $Expected, $Actual)
    Assert-That -Description $Description -Condition ("$Expected" -ceq "$Actual") -Detail "expected [$Expected] but the request carried [$Actual]"
}

function Get-Prop {
    param($InputObject, [string]$Name)
    if ($null -eq $InputObject) { return $null }
    $prop = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $prop) { return $null }
    return $prop.Value
}

function Get-MemberSet {
    param($InputObject)
    if ($null -eq $InputObject) { return '<none>' }
    return (($InputObject.PSObject.Properties.Name | Sort-Object) -join ',')
}

# Walks a decoded request body and reports every member that was put on the wire
# without a value. The contract requires unset optional members to be absent.
function Get-EmptyMember {
    param($Node, [string]$Path)
    $found = [System.Collections.Generic.List[string]]::new()
    if ($null -eq $Node) { $found.Add("$Path = null"); return $found.ToArray() }
    if ($Node -is [string]) {
        if ($Node -eq '') { $found.Add("$Path = empty string") }
        return $found.ToArray()
    }
    if ($Node -is [System.ValueType]) { return $found.ToArray() }
    if ($Node -is [System.Collections.IEnumerable]) {
        $items = @($Node)
        if ($items.Count -eq 0) { $found.Add("$Path = empty array"); return $found.ToArray() }
        for ($i = 0; $i -lt $items.Count; $i++) {
            foreach ($v in @(Get-EmptyMember -Node $items[$i] -Path "$Path[$i]")) { $found.Add($v) }
        }
        return $found.ToArray()
    }
    $names = @($Node.PSObject.Properties.Name)
    if ($names.Count -eq 0) { $found.Add("$Path = empty object"); return $found.ToArray() }
    foreach ($name in $names) {
        foreach ($v in @(Get-EmptyMember -Node $Node.$name -Path "$Path.$name")) { $found.Add($v) }
    }
    return $found.ToArray()
}

function Invoke-RegistrationScenario {
    param(
        [string[]]$FixtureArgument = @(),
        [string]$ItemName = 'supplemental-item',
        [Nullable[int]]$MaxAttempts
    )

    $scenarioDir = Join-Path ([System.IO.Path]::GetTempPath()) ("vcf90-0036-scenario-" + [Guid]::NewGuid().ToString('n'))
    $null = New-Item -ItemType Directory -Path $scenarioDir
    $scenarioLog = Join-Path $scenarioDir 'requests.jsonl'
    $scenarioReady = Join-Path $scenarioDir 'ready.json'
    $scenarioStderr = Join-Path $scenarioDir 'fixture.err'
    $scenarioFixture = $null
    $scenarioResult = $null
    $scenarioError = $null
    $sleepDelays = [System.Collections.Generic.List[double]]::new()
    $sleepAction = { param($seconds) $sleepDelays.Add([double]$seconds) }.GetNewClosure()

    try {
        $arguments = @($mockPath, '--contract', $contractPath, '--log', $scenarioLog, '--ready', $scenarioReady) + $FixtureArgument
        $scenarioFixture = Start-Process -FilePath 'python3' -PassThru -NoNewWindow `
            -RedirectStandardError $scenarioStderr -ArgumentList $arguments

        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        while (-not (Test-Path $scenarioReady)) {
            if ($scenarioFixture.HasExited) {
                throw "The supplemental fixture exited before becoming ready: $(Get-Content -Raw -ErrorAction SilentlyContinue $scenarioStderr)"
            }
            if ([DateTime]::UtcNow -gt $deadline) {
                throw "The supplemental fixture did not start: $(Get-Content -Raw -ErrorAction SilentlyContinue $scenarioStderr)"
            }
            Start-Sleep -Milliseconds 100
        }
        $scenarioServer = (Get-Content -Raw $scenarioReady | ConvertFrom-Json).baseUrl
        $password = ConvertTo-SecureString 'VMw@re1!Catalog' -AsPlainText -Force
        $credential = [pscredential]::new('svc-catalog@vsphere.local', $password)
        $invokeArguments = @{
            Server       = $scenarioServer
            Credential   = $credential
            LibraryName  = 'sfo-w01-cl01'
            Item         = @([pscustomobject]@{ Name = $ItemName })
            SleepAction  = $sleepAction
        }
        if ($PSBoundParameters.ContainsKey('MaxAttempts')) {
            $invokeArguments['MaxAttempts'] = $MaxAttempts.Value
        }
        try {
            $scenarioResult = Invoke-VcfLibraryItemRegistration @invokeArguments
        }
        catch {
            $scenarioError = $_
        }
    }
    finally {
        if ($scenarioFixture -and -not $scenarioFixture.HasExited) {
            Stop-Process -Id $scenarioFixture.Id -Force -ErrorAction SilentlyContinue
            $scenarioFixture.WaitForExit(10000) | Out-Null
        }
    }

    $scenarioRequests = @()
    if (Test-Path $scenarioLog) {
        $scenarioRequests = @(Get-Content -Path $scenarioLog | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
    }
    Remove-Item -Recurse -Force $scenarioDir -ErrorAction SilentlyContinue
    return [pscustomobject]@{
        Result      = $scenarioResult
        Error       = $scenarioError
        SleepDelays = @($sleepDelays.ToArray())
        Log         = $scenarioRequests
    }
}

$fixture = $null
try {
    if (-not (Test-Path $modulePath)) { throw "VcfLibraryItemRegistration.psm1 was not found at the workspace root." }

    $moduleSource = Get-Content -Raw $modulePath
    $parseTokens = $null
    $parseErrors = $null
    $moduleAst = [System.Management.Automation.Language.Parser]::ParseInput(
        $moduleSource, [ref]$parseTokens, [ref]$parseErrors
    )
    Assert-Equal -Description 'the module source parses without PowerShell syntax errors' -Expected 0 -Actual @($parseErrors).Count
    $commandAsts = @($moduleAst.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true))
    $commandNames = @($commandAsts | ForEach-Object { $_.GetCommandName() })
    foreach ($initializer in @(
            'Initialize-ContentLibraryFindSpec',
            'Initialize-ContentLibraryItemFindSpec',
            'Initialize-ContentLibraryItemModel'
        )) {
        Assert-That -Description "the module builds requests with $initializer" `
            -Condition (@($commandNames | Where-Object { $_ -eq $initializer -or $_ -like "*\$initializer" }).Count -ge 1)
    }

    $fixture = Start-Process -FilePath 'python3' -PassThru -NoNewWindow -RedirectStandardError $stderrPath `
        -ArgumentList @($mockPath, '--contract', $contractPath, '--log', $logPath, '--ready', $readyPath)

    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while (-not (Test-Path $readyPath)) {
        if ($fixture.HasExited) { throw "The loopback vCenter fixture exited before becoming ready: $(Get-Content -Raw -ErrorAction SilentlyContinue $stderrPath)" }
        if ([DateTime]::UtcNow -gt $deadline) { throw "The loopback vCenter fixture did not start: $(Get-Content -Raw -ErrorAction SilentlyContinue $stderrPath)" }
        Start-Sleep -Milliseconds 100
    }
    $ready = Get-Content -Raw $readyPath | ConvertFrom-Json
    $server = $ready.baseUrl

    Import-Module $modulePath -Force -ErrorAction Stop -WarningAction SilentlyContinue 3>$null

    Assert-That -Description 'the module imports the genuine VMware.Sdk.vSphere.ContentLibrary package' `
        -Condition ($null -ne (Get-Module -All -Name 'VMware.Sdk.vSphere.ContentLibrary'))

    $exported = @(Get-Command -Module 'VcfLibraryItemRegistration' | Select-Object -ExpandProperty Name | Sort-Object)
    Assert-Equal -Description 'the module exports only Invoke-VcfLibraryItemRegistration' `
        -Expected 'Invoke-VcfLibraryItemRegistration' -Actual ($exported -join ',')

    $password = ConvertTo-SecureString 'VMw@re1!Catalog' -AsPlainText -Force
    $credential = [pscredential]::new('svc-catalog@vsphere.local', $password)

    $requested = @(
        [pscustomobject]@{ Name = 'photon-5.0-ova'; Type = 'ovf' }
        [pscustomobject]@{ Name = 'sfo-w01-runbook-bundle' }
        [pscustomobject]@{ Name = 'esx-9.0-image-profile'; Type = 'ovf' }
    )

    $mainSleepDelays = [System.Collections.Generic.List[double]]::new()
    $mainSleepAction = { param($seconds) $mainSleepDelays.Add([double]$seconds) }.GetNewClosure()
    $result = Invoke-VcfLibraryItemRegistration -Server $server -Credential $credential `
        -LibraryName 'sfo-w01-cl01' -Item $requested -SleepAction $mainSleepAction
}
finally {
    if ($fixture -and -not $fixture.HasExited) {
        Stop-Process -Id $fixture.Id -Force -ErrorAction SilentlyContinue
        $fixture.WaitForExit(10000) | Out-Null
    }
}

$log = @()
if (Test-Path $logPath) {
    $log = @(Get-Content -Path $logPath | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
}

$byOp = { param([string]$Op) @($log | Where-Object { $_.operationId -eq $Op }) }

# ---------------------------------------------------------------- traffic ----
Assert-That -Description 'every request the module made resolved to an operation named by docs/contract.json' `
    -Condition (@($log | Where-Object { $null -eq $_.operationId }).Count -eq 0) `
    -Detail ("off-contract requests: " + ((@($log | Where-Object { $null -eq $_.operationId }) | ForEach-Object { "$($_.method) $($_.target)" }) -join '; '))

Assert-Equal -Description 'the module made exactly eleven requests' -Expected 11 -Actual $log.Count

$sessions = & $byOp 'Cis.Session_create'
$libFinds = & $byOp 'Content.Library_find'
$itemFinds = & $byOp 'Content.Library.Item_find'
$creates = & $byOp 'Content.Library.Item_create'
$gets = & $byOp 'Content.Library.Item_get'

Assert-Equal -Description 'Cis.Session_create is called exactly once for the whole run' -Expected 1 -Actual $sessions.Count
Assert-Equal -Description 'Content.Library_find is called exactly once' -Expected 1 -Actual $libFinds.Count
Assert-Equal -Description 'Content.Library.Item_find is called once per requested item' -Expected 3 -Actual $itemFinds.Count
Assert-Equal -Description 'Content.Library.Item_get is called once per requested item' -Expected 3 -Actual $gets.Count

# ---------------------------------------------------------------- session ----
if ($sessions.Count -ge 1) {
    $s = $sessions[0]
    Assert-Equal -Description 'the session is the first request on the wire' -Expected 1 -Actual $s.seq
    Assert-Equal -Description 'Cis.Session_create uses POST /api/session' -Expected 'POST /api/session' -Actual "$($s.method) $($s.path)"
    Assert-Equal -Description 'Cis.Session_create carries no query string' -Expected '' -Actual $s.query
    Assert-Equal -Description 'Cis.Session_create sends no request body' -Expected '' -Actual $s.body
    $auth = Get-Prop $s.headers 'authorization'
    Assert-That -Description 'Cis.Session_create authenticates with HTTP Basic' -Condition ($auth -is [string] -and $auth.StartsWith('Basic ')) -Detail "Authorization was [$auth]"
    if ($auth -is [string] -and $auth.StartsWith('Basic ')) {
        $decoded = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($auth.Substring(6)))
        Assert-Equal -Description 'the Basic credential is the caller credential' -Expected 'svc-catalog@vsphere.local:VMw@re1!Catalog' -Actual $decoded
    }
    Assert-That -Description 'Cis.Session_create does not send a vmware-api-session-id header' `
        -Condition ($null -eq (Get-Prop $s.headers 'vmware-api-session-id'))
}

$authenticated = @($log | Where-Object { $_.operationId -and $_.operationId -ne 'Cis.Session_create' })
foreach ($r in $authenticated) {
    Assert-Equal -Description "$($r.operationId) (seq $($r.seq)) carries the session token in vmware-api-session-id" `
        -Expected 'sess-9f2c1d8a4b7e' -Actual (Get-Prop $r.headers 'vmware-api-session-id')
    Assert-That -Description "$($r.operationId) (seq $($r.seq)) sends no Authorization header once the session exists" `
        -Condition ($null -eq (Get-Prop $r.headers 'authorization'))
}

# ------------------------------------------------------------ empty members ----
foreach ($r in $log) {
    if (-not $r.body) { continue }
    $decoded = $r.body | ConvertFrom-Json
    $empties = @(Get-EmptyMember -Node $decoded -Path 'body')
    Assert-That -Description "$($r.operationId) (seq $($r.seq)) omits every unset optional member instead of sending it empty" `
        -Condition ($empties.Count -eq 0) -Detail ("sent " + ($empties -join '; ') + " in " + $r.body)
}

# ------------------------------------------------------------- library find ----
if ($libFinds.Count -ge 1) {
    $f = $libFinds[0]
    Assert-Equal -Description 'Content.Library_find uses POST /api/content/library' -Expected 'POST /api/content/library' -Actual "$($f.method) $($f.path)"
    Assert-Equal -Description 'Content.Library_find is selected with the action=find query string' -Expected 'action=find' -Actual $f.query
    Assert-That -Description 'Content.Library_find declares a JSON request body' `
        -Condition ((Get-Prop $f.headers 'content-type') -match 'application/json') -Detail "Content-Type was [$(Get-Prop $f.headers 'content-type')]"
    $spec = $f.body | ConvertFrom-Json
    Assert-Equal -Description 'the Content.Library.FindSpec carries name and type and nothing else' -Expected 'name,type' -Actual (Get-MemberSet $spec)
    Assert-Equal -Description 'the Content.Library.FindSpec filters on the requested library name' -Expected 'sfo-w01-cl01' -Actual (Get-Prop $spec 'name')
    Assert-Equal -Description 'the Content.Library.FindSpec filters on LOCAL libraries' -Expected 'LOCAL' -Actual (Get-Prop $spec 'type')
    Assert-That -Description 'Content.Library_find happens after the session is created' -Condition ($f.seq -gt 1)
}

# ---------------------------------------------------------------- item find ----
$expectedNames = @('photon-5.0-ova', 'sfo-w01-runbook-bundle', 'esx-9.0-image-profile')
$findNames = @()
foreach ($f in $itemFinds) {
    Assert-Equal -Description "Content.Library.Item_find (seq $($f.seq)) uses POST /api/content/library/item" -Expected 'POST /api/content/library/item' -Actual "$($f.method) $($f.path)"
    Assert-Equal -Description "Content.Library.Item_find (seq $($f.seq)) is selected with the action=find query string" -Expected 'action=find' -Actual $f.query
    $spec = $f.body | ConvertFrom-Json
    Assert-Equal -Description "the Content.Library.Item.FindSpec (seq $($f.seq)) carries library_id and name and nothing else" -Expected 'library_id,name' -Actual (Get-MemberSet $spec)
    Assert-Equal -Description "the Content.Library.Item.FindSpec (seq $($f.seq)) scopes the search to the resolved library" -Expected 'lib-sfo-w01-cl01' -Actual (Get-Prop $spec 'library_id')
    $findNames += (Get-Prop $spec 'name')
}
Assert-Equal -Description 'exactly one Content.Library.Item_find per requested item name' `
    -Expected (($expectedNames | Sort-Object) -join ',') -Actual ((@($findNames) | Sort-Object) -join ',')

# ------------------------------------------------------------- item create ----
Assert-Equal -Description 'the module creates only the two items that are missing, and retries the interrupted one once' -Expected 3 -Actual $creates.Count

$createsByName = @{}
foreach ($c in $creates) {
    Assert-Equal -Description "Content.Library.Item_create (seq $($c.seq)) uses POST /api/content/library/item" -Expected 'POST /api/content/library/item' -Actual "$($c.method) $($c.path)"
    Assert-Equal -Description "Content.Library.Item_create (seq $($c.seq)) carries no query string" -Expected '' -Actual $c.query
    $model = $c.body | ConvertFrom-Json
    Assert-Equal -Description "the Content.Library.ItemModel (seq $($c.seq)) targets the resolved library" -Expected 'lib-sfo-w01-cl01' -Actual (Get-Prop $model 'library_id')
    $name = Get-Prop $model 'name'
    if (-not $createsByName.ContainsKey($name)) { $createsByName[$name] = @() }
    $createsByName[$name] += , $c
}

Assert-Equal -Description 'creates are attempted for exactly the two missing items' `
    -Expected 'photon-5.0-ova,sfo-w01-runbook-bundle' -Actual ((@($createsByName.Keys) | Sort-Object) -join ',')
Assert-That -Description 'the already registered item is never created again' -Condition (-not $createsByName.ContainsKey('esx-9.0-image-profile'))

if ($createsByName.ContainsKey('photon-5.0-ova')) {
    $attempts = @($createsByName['photon-5.0-ova'])
    Assert-Equal -Description 'the create interrupted by 503 is attempted exactly twice' -Expected 2 -Actual $attempts.Count
    foreach ($a in $attempts) {
        $model = $a.body | ConvertFrom-Json
        Assert-Equal -Description "the typed Content.Library.ItemModel (seq $($a.seq)) carries library_id, name and type and nothing else" `
            -Expected 'library_id,name,type' -Actual (Get-MemberSet $model)
        Assert-Equal -Description "the typed Content.Library.ItemModel (seq $($a.seq)) carries the requested type" -Expected 'ovf' -Actual (Get-Prop $model 'type')
    }
    $tokens = @($attempts | ForEach-Object { Get-Prop $_.headers 'client-token' })
    Assert-That -Description 'the retry replays the create with the Client-Token of the first attempt, so vCenter cannot create a second item' `
        -Condition ((@($tokens | Select-Object -Unique)).Count -eq 1 -and [bool]$tokens[0]) -Detail ("Client-Token per attempt: " + ($tokens -join ' | '))
    Assert-Equal -Description 'the retried create is answered as an idempotent replay rather than a second creation' `
        -Expected 'created-then-503,idempotent-replay' -Actual ((@($attempts | ForEach-Object { $_.mockNote })) -join ',')
}
Assert-Equal -Description 'SleepAction receives the retry delay instead of the module sleeping directly' `
    -Expected '1' -Actual ((@($mainSleepDelays.ToArray())) -join ',')

if ($createsByName.ContainsKey('sfo-w01-runbook-bundle')) {
    $attempts = @($createsByName['sfo-w01-runbook-bundle'])
    Assert-Equal -Description 'the untyped item is created in a single attempt' -Expected 1 -Actual $attempts.Count
    $model = $attempts[0].body | ConvertFrom-Json
    Assert-Equal -Description 'the untyped Content.Library.ItemModel omits type and carries library_id and name only' `
        -Expected 'library_id,name' -Actual (Get-MemberSet $model)
}

$allTokens = @($creates | ForEach-Object { Get-Prop $_.headers 'client-token' })
foreach ($t in $allTokens) {
    Assert-That -Description "the Client-Token [$t] is a lowercase UUID as the specification requires" `
        -Condition ($t -is [string] -and $t -cmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
}
Assert-Equal -Description 'each item gets its own Client-Token' -Expected 2 -Actual (@($allTokens | Select-Object -Unique)).Count

foreach ($r in @($log | Where-Object { $_.operationId -and $_.operationId -ne 'Content.Library.Item_create' })) {
    Assert-That -Description "$($r.operationId) (seq $($r.seq)) does not send a Client-Token header" `
        -Condition ($null -eq (Get-Prop $r.headers 'client-token'))
}

Assert-Equal -Description 'exactly two library items are created across the whole run' `
    -Expected 2 -Actual (@($creates | Where-Object { $_.mockNote -eq 'created' -or $_.mockNote -eq 'created-then-503' })).Count
Assert-Equal -Description 'no create is rejected because a duplicate item already exists' `
    -Expected 0 -Actual (@($creates | Where-Object { $_.mockNote -eq 'already-exists' })).Count

# ---------------------------------------------------------------- item get ----
$getIds = @($gets | ForEach-Object {
        Assert-Equal -Description "Content.Library.Item_get (seq $($_.seq)) is a GET with no body" -Expected 'GET ' -Actual "$($_.method) $($_.body)"
        Assert-Equal -Description "Content.Library.Item_get (seq $($_.seq)) carries no query string" -Expected '' -Actual $_.query
        $_.pathParameters.libraryItemId
    })
Assert-Equal -Description 'each registered item is read back by its identifier' `
    -Expected 'item-0001,item-0002,item-0007' -Actual ((@($getIds) | Sort-Object) -join ',')

# ----------------------------------------------------------------- ordering ----
foreach ($name in @('photon-5.0-ova', 'sfo-w01-runbook-bundle')) {
    if (-not $createsByName.ContainsKey($name)) { continue }
    $find = @($itemFinds | Where-Object { (Get-Prop ($_.body | ConvertFrom-Json) 'name') -eq $name })
    if ($find.Count -eq 1) {
        $firstCreate = @($createsByName[$name] | Sort-Object seq)[0]
        Assert-That -Description "the find for $name precedes its create, so an item that already exists is never re-created" `
            -Condition ($find[0].seq -lt $firstCreate.seq)
    }
}

# ------------------------------------------------------------------- result ----
Assert-That -Description 'the function returns a result object' -Condition ($null -ne $result)
if ($null -ne $result) {
    Assert-Equal -Description 'the result reports the resolved library identifier' -Expected 'lib-sfo-w01-cl01' -Actual (Get-Prop $result 'LibraryId')
    $items = @(Get-Prop $result 'Items')
    Assert-Equal -Description 'the result reports one entry per requested item' -Expected 3 -Actual $items.Count
    $expected = @{
        'photon-5.0-ova'         = @{ ItemId = 'item-0001'; Created = $true }
        'sfo-w01-runbook-bundle' = @{ ItemId = 'item-0002'; Created = $true }
        'esx-9.0-image-profile'  = @{ ItemId = 'item-0007'; Created = $false }
    }
    Assert-Equal -Description 'the result keeps the requested item order' `
        -Expected 'photon-5.0-ova,sfo-w01-runbook-bundle,esx-9.0-image-profile' `
        -Actual ((@($items | ForEach-Object { Get-Prop $_ 'Name' })) -join ',')
    foreach ($entry in $items) {
        $name = Get-Prop $entry 'Name'
        if (-not $expected.ContainsKey($name)) {
            Assert-That -Description "the result reports a requested item, not [$name]" -Condition $false
            continue
        }
        Assert-Equal -Description "the result reports the library item identifier for $name" -Expected $expected[$name].ItemId -Actual (Get-Prop $entry 'ItemId')
        Assert-Equal -Description "the result reports whether $name was created by this run" -Expected $expected[$name].Created -Actual (Get-Prop $entry 'Created')
    }
}

# ------------------------------------------------------ retry edge cases ----
$defaultAttempts = Invoke-RegistrationScenario `
    -FixtureArgument @('--precommit-create-failures', '2') -ItemName 'default-attempts-item'
$defaultCreates = @($defaultAttempts.Log | Where-Object { $_.operationId -eq 'Content.Library.Item_create' })
Assert-That -Description 'the default MaxAttempts value permits a third create attempt' `
    -Condition ($null -eq $defaultAttempts.Error) -Detail "$($defaultAttempts.Error)"
Assert-Equal -Description 'the default MaxAttempts value is three' -Expected 3 -Actual $defaultCreates.Count
Assert-Equal -Description 'SleepAction receives each default-attempt retry delay' -Expected '1,2' `
    -Actual ((@($defaultAttempts.SleepDelays)) -join ',')
Assert-Equal -Description 'the default-attempt scenario still finds only once rather than recovering with another find' `
    -Expected 1 -Actual @($defaultAttempts.Log | Where-Object { $_.operationId -eq 'Content.Library.Item_find' }).Count

$attemptCeiling = Invoke-RegistrationScenario `
    -FixtureArgument @('--precommit-create-failures', '3') -ItemName 'attempt-ceiling-item' -MaxAttempts 2
$ceilingCreates = @($attemptCeiling.Log | Where-Object { $_.operationId -eq 'Content.Library.Item_create' })
Assert-That -Description 'a create that exhausts MaxAttempts fails' -Condition ($null -ne $attemptCeiling.Error)
Assert-Equal -Description 'MaxAttempts is the exact create-attempt ceiling' -Expected 2 -Actual $ceilingCreates.Count
Assert-Equal -Description 'no sleep is requested after the final allowed attempt' -Expected '1' `
    -Actual ((@($attemptCeiling.SleepDelays)) -join ',')

$terminalFourxx = Invoke-RegistrationScenario `
    -FixtureArgument @('--reject-creates-with-400') -ItemName 'terminal-fourxx-item' -MaxAttempts 3
$fourxxCreates = @($terminalFourxx.Log | Where-Object { $_.operationId -eq 'Content.Library.Item_create' })
Assert-That -Description 'a 4xx create response is terminal' -Condition ($null -ne $terminalFourxx.Error)
Assert-Equal -Description 'a 4xx create response is never retried' -Expected 1 -Actual $fourxxCreates.Count
Assert-Equal -Description 'a terminal 4xx does not invoke SleepAction' -Expected 0 -Actual @($terminalFourxx.SleepDelays).Count

# --------------------------------------------------------------- no vendoring ----
$vendored = @(Get-ChildItem -Path $root -Directory -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like 'VMware.*' } | Select-Object -ExpandProperty FullName)
Assert-That -Description 'no VMware SDK module is vendored into the repository' -Condition ($vendored.Count -eq 0) `
    -Detail ($vendored -join '; ')

Remove-Item -Recurse -Force $workDir -ErrorAction SilentlyContinue

if ($script:Failures.Count -gt 0) {
    Write-Host ""
    foreach ($f in $script:Failures) { Write-Host $f }
    Write-Host ""
    Write-Host ("{0} of {1} checks failed." -f $script:Failures.Count, $script:Checks)
    exit 1
}

Write-Host ("All {0} checks passed." -f $script:Checks)
exit 0
