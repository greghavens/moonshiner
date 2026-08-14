Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$contractPath = Join-Path $root 'docs/contract.json'
$sourcesPath = Join-Path $root 'docs/official_sources.json'
$modulePath = Join-Path $root 'VcfAutomation.Policy/VcfAutomation.Policy.psm1'
$manifestPath = Join-Path $root 'VcfAutomation.Policy/VcfAutomation.Policy.psd1'
$mockPath = Join-Path $PSScriptRoot 'mock_vcf_automation.py'
$script:assertions = 0

function Assert-True {
    param([bool] $Condition, [string] $Message)
    $script:assertions++
    if (-not $Condition) { throw "Assertion failed: $Message" }
}

function Assert-Equal {
    param($Actual, $Expected, [string] $Message)
    $script:assertions++
    if ($Actual -cne $Expected) {
        throw "Assertion failed: $Message`nExpected: $Expected`nActual:   $Actual"
    }
}

function Get-LogRecords {
    param([string] $Path)
    return @(
        Get-Content -LiteralPath $Path |
            Where-Object { $_.Trim() } |
            ForEach-Object { $_ | ConvertFrom-Json -Depth 30 }
    )
}

function Start-Mock {
    param([int] $PrecheckStatus = 200, [int] $MutationStatus = 201)
    $caseRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("vcf-automation-" + [guid]::NewGuid())
    $null = New-Item -ItemType Directory -Path $caseRoot
    $logPath = Join-Path $caseRoot 'requests.jsonl'
    $portPath = Join-Path $caseRoot 'port.txt'
    $arguments = @(
        $mockPath,
        '--contract', $contractPath,
        '--log', $logPath,
        '--port-file', $portPath,
        '--token', 'fixture-token',
        '--precheck-status', $PrecheckStatus,
        '--mutation-status', $MutationStatus
    )
    $process = Start-Process -FilePath 'python3' -ArgumentList $arguments -PassThru -NoNewWindow
    $deadline = [datetime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path -LiteralPath $portPath)) {
        if ($process.HasExited) { throw "Loopback mock exited during startup with $($process.ExitCode)." }
        if ([datetime]::UtcNow -gt $deadline) { throw 'Timed out starting the loopback mock.' }
        Start-Sleep -Milliseconds 25
    }
    $port = [int](Get-Content -LiteralPath $portPath -Raw)
    return [pscustomobject]@{
        Process = $process
        Root = $caseRoot
        Log = $logPath
        Uri = [uri]"http://127.0.0.1:$port/"
    }
}

function Stop-Mock {
    param($Mock)
    if ($null -ne $Mock.Process -and -not $Mock.Process.HasExited) {
        Stop-Process -Id $Mock.Process.Id -Force
        $Mock.Process.WaitForExit()
    }
    if (Test-Path -LiteralPath $Mock.Root) {
        Remove-Item -LiteralPath $Mock.Root -Recurse -Force
    }
}

function Wait-ForRecords {
    param([string] $Path, [int] $Count)
    $deadline = [datetime]::UtcNow.AddSeconds(5)
    do {
        $records = Get-LogRecords -Path $Path
        if ($records.Count -ge $Count) { return $records }
        Start-Sleep -Milliseconds 20
    } while ([datetime]::UtcNow -lt $deadline)
    throw "Timed out waiting for $Count request log records; found $($records.Count)."
}

# Contract and provenance assertions are deliberately local; verification never
# fetches the official page or contacts any VMware endpoint.
$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json -Depth 50
Assert-Equal $contract.product 'VMware Cloud Foundation Automation' 'contract product'
Assert-Equal $contract.version '9.0' 'contract version'
Assert-True ($contract.source.kind -match 'reference') 'source kind identifies a reference'
Assert-True ($contract.source.statement -match 'reference documentation') 'source statement identifies reference documentation'
Assert-True ($contract.source.statement -match 'not a published specification') 'source statement disclaims a published specification'
Assert-Equal @($contract.operations).Count 1 'contract names exactly one xAPI operation'
$operation = @($contract.operations)[0]
Assert-True (-not [string]::IsNullOrWhiteSpace($operation.id)) 'operation id is present'
Assert-Equal $operation.documentedName 'Create Policy 1' 'documented operation name'
Assert-Equal $operation.method 'POST' 'operation method'
Assert-Equal $operation.path '/policy/api/policies' 'operation path'
Assert-Equal $operation.authentication.scheme 'bearerAuth' 'authentication scheme'
Assert-Equal $operation.authentication.header 'Authorization' 'authentication header'
Assert-Equal $operation.authentication.valueFormat 'Bearer {access-token}' 'authentication value format'
Assert-Equal $operation.headers.accept 'application/json' 'accept header value'
Assert-Equal $operation.headers.contentType 'application/json' 'content type header value'
Assert-Equal $operation.requestBody.contentType 'application/json' 'request media type'
Assert-Equal @($operation.requestBody.required).Count 1 'one required request field'
Assert-Equal @($operation.requestBody.required)[0] 'typeId' 'required typeId field'
Assert-Equal $operation.modes.precheck.query.validationOnly $true 'precheck query flag'
Assert-Equal $operation.modes.precheck.mutating $false 'precheck is non-mutating'
Assert-Equal @($operation.modes.mutate.query.psobject.Properties).Count 0 'mutation has no query fields'
Assert-Equal $operation.modes.mutate.mutating $true 'second request is mutating'
foreach ($modeName in @('precheck', 'mutate')) {
    $successStatuses = @($operation.modes.$modeName.successStatus)
    Assert-Equal $successStatuses.Count 3 "$modeName success status count"
    foreach ($status in @(200, 201, 202)) {
        Assert-True ($successStatuses -contains $status) "$modeName supports HTTP $status"
    }
}

$expectedOptional = @('id', 'name', 'description', 'projectId', 'enforcementType', 'definition', 'criteria', 'scopeCriteria', 'opaRegoCriteria')
Assert-Equal @($operation.requestBody.optional).Count $expectedOptional.Count 'supported optional property count'
foreach ($propertyName in $expectedOptional) {
    Assert-True (@($operation.requestBody.optional) -ccontains $propertyName) "supported optional property $propertyName"
}

$sources = Get-Content -LiteralPath $sourcesPath -Raw | ConvertFrom-Json -Depth 20
Assert-True (@($sources.sources).Count -ge 1) 'at least one official reference page is recorded'
$matchingSources = @($sources.sources | Where-Object { $_.url -ceq 'https://developer.broadcom.com/xapis/vm-apps-org-policies/9.0/policy/api/policies/post/' })
Assert-Equal $matchingSources.Count 1 'version-pinned operation reference URL is recorded once'
$source = $matchingSources[0]
Assert-Equal $source.url 'https://developer.broadcom.com/xapis/vm-apps-org-policies/9.0/policy/api/policies/post/' 'version-pinned official URL'
Assert-Equal $source.operation 'Create Policy 1' 'source operation label'
foreach ($recordedSource in @($sources.sources)) {
    Assert-True ($recordedSource.url -match '^https://developer\.broadcom\.com/xapis/vm-apps-org-policies/9\.0/') 'source is a version-pinned official reference page'
    Assert-True (-not [string]::IsNullOrWhiteSpace($recordedSource.operation)) 'source operation label is present'
    $fetchedDate = [datetime]::MinValue
    Assert-True ([datetime]::TryParseExact($recordedSource.fetched, 'yyyy-MM-dd', [cultureinfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$fetchedDate)) 'source fetch date format'
}

$manifest = Import-PowerShellDataFile -LiteralPath $manifestPath
$matchingModules = @($manifest.RequiredModules | Where-Object { $_.ModuleName -ceq 'VMware.Sdk.Vcf.SddcManager' })
Assert-Equal $matchingModules.Count 1 'PowerCLI SDK prerequisite is declared once'
$requiredModule = $matchingModules[0]
Assert-Equal $requiredModule.ModuleName 'VMware.Sdk.Vcf.SddcManager' 'PowerCLI SDK prerequisite name'
$declaredVersion = $null
foreach ($versionKey in @('RequiredVersion', 'ModuleVersion')) {
    if ($requiredModule.ContainsKey($versionKey)) {
        $declaredVersion = $requiredModule[$versionKey]
        break
    }
}
Assert-True ($null -ne $declaredVersion) 'PowerCLI SDK prerequisite has a version constraint'
Assert-Equal $declaredVersion.ToString() '13.4.0.24798382' 'PowerCLI SDK prerequisite version'

Import-Module -Name $modulePath -Force
$command = Get-Command Set-VcfAutomationPolicy -ErrorAction Stop
Assert-Equal $command.CommandType 'Function' 'module exports Set-VcfAutomationPolicy'

# Minimal success: exact targets and raw body prove unset optionals are absent,
# rather than serialized as empty strings or null values.
$mock = Start-Mock
try {
    $null = Set-VcfAutomationPolicy -Server $mock.Uri -AccessToken 'fixture-token' -TypeId 'com.vmware.policy.deployment.limit'
    $records = Wait-ForRecords -Path $mock.Log -Count 2
    Assert-Equal $records.Count 2 'successful call emits precheck and mutation only'
    Assert-Equal $records[0].method 'POST' 'precheck method'
    Assert-Equal $records[0].target '/policy/api/policies?validationOnly=true' 'precheck request target'
    Assert-Equal $records[0].mode 'precheck' 'precheck route classification'
    Assert-Equal $records[1].method 'POST' 'mutation method'
    Assert-Equal $records[1].target '/policy/api/policies' 'mutation request target has no query'
    Assert-Equal $records[1].mode 'mutate' 'mutation route classification'
    foreach ($record in $records) {
        Assert-Equal $record.headers.authorization 'Bearer fixture-token' 'bearer authorization header'
        Assert-Equal $record.headers.accept 'application/json' 'JSON accept header'
        Assert-True ($record.headers.'content-type' -match '^application/json(?:;|$)') 'JSON content type header'
        Assert-Equal $record.rawBody '{"typeId":"com.vmware.policy.deployment.limit"}' 'minimal raw JSON wire body'
        Assert-Equal @($record.jsonBody.psobject.Properties).Count 1 'unset optional fields are omitted'
    }
    Assert-Equal $records[0].rawBody $records[1].rawBody 'validated and mutated documents are byte-identical'
}
finally {
    Stop-Mock $mock
}

# Supplied optionals must be included with their contract wire names in both calls.
$mock = Start-Mock
try {
    $definition = [ordered]@{ limit = 2 }
    $criteria = [ordered]@{ matchExpression = @() }
    $scopeCriteria = [ordered]@{ matchExpression = @() }
    $null = Set-VcfAutomationPolicy -Server $mock.Uri -AccessToken 'fixture-token' `
        -TypeId 'com.vmware.policy.deployment.limit' `
        -Id '11111111-1111-1111-1111-111111111111' `
        -Name 'Project deployment limit' `
        -Description 'Two deployments' `
        -ProjectId 'project-42' `
        -EnforcementType 'HARD' `
        -Definition $definition `
        -Criteria $criteria `
        -ScopeCriteria $scopeCriteria `
        -OpaRegoCriteria 'package policy'
    $records = Wait-ForRecords -Path $mock.Log -Count 2
    Assert-Equal $records.Count 2 'optional success emits exactly two calls'
    Assert-Equal $records[0].rawBody $records[1].rawBody 'full validated and mutated documents are byte-identical'
    foreach ($record in $records) {
        Assert-Equal @($record.jsonBody.psobject.Properties).Count 10 'full body has exactly the supported properties'
        Assert-Equal $record.jsonBody.typeId 'com.vmware.policy.deployment.limit' 'full body typeId'
        Assert-Equal $record.jsonBody.id '11111111-1111-1111-1111-111111111111' 'full body id'
        Assert-Equal $record.jsonBody.name 'Project deployment limit' 'full body name'
        Assert-Equal $record.jsonBody.description 'Two deployments' 'full body description'
        Assert-Equal $record.jsonBody.projectId 'project-42' 'full body projectId'
        Assert-Equal $record.jsonBody.enforcementType 'HARD' 'full body enforcementType'
        Assert-Equal $record.jsonBody.definition.limit 2 'full body definition'
        Assert-Equal @($record.jsonBody.criteria.matchExpression).Count 0 'full body criteria'
        Assert-Equal @($record.jsonBody.scopeCriteria.matchExpression).Count 0 'full body scopeCriteria'
        Assert-Equal $record.jsonBody.opaRegoCriteria 'package policy' 'full body opaRegoCriteria'
    }
}
finally {
    Stop-Mock $mock
}

# A sparse optional selection must not cause the other supported fields to appear.
$mock = Start-Mock
try {
    $null = Set-VcfAutomationPolicy -Server $mock.Uri -AccessToken 'fixture-token' `
        -TypeId 'com.vmware.policy.deployment.limit' `
        -Name 'Sparse policy' `
        -Definition ([ordered]@{ limit = 5 })
    $records = Wait-ForRecords -Path $mock.Log -Count 2
    foreach ($record in $records) {
        $propertyNames = @($record.jsonBody.psobject.Properties.Name)
        Assert-Equal $propertyNames.Count 3 'sparse body property count'
        Assert-True ($propertyNames -ccontains 'typeId') 'sparse body includes typeId'
        Assert-True ($propertyNames -ccontains 'name') 'sparse body includes supplied name'
        Assert-True ($propertyNames -ccontains 'definition') 'sparse body includes supplied definition'
        Assert-Equal $record.jsonBody.name 'Sparse policy' 'sparse body name value'
        Assert-Equal $record.jsonBody.definition.limit 5 'sparse body definition value'
    }
    Assert-Equal $records[0].rawBody $records[1].rawBody 'sparse validated and mutated documents are byte-identical'
}
finally {
    Stop-Mock $mock
}

# Every documented 2xx status is successful, including asynchronous validation.
$mock = Start-Mock -PrecheckStatus 202 -MutationStatus 200
try {
    $null = Set-VcfAutomationPolicy -Server $mock.Uri -AccessToken 'fixture-token' -TypeId 'com.vmware.policy.deployment.limit'
    $records = Wait-ForRecords -Path $mock.Log -Count 2
    Assert-Equal $records.Count 2 'alternate documented success statuses emit both calls'
    Assert-Equal $records[0].responseStatus 202 'precheck accepts documented 202 status'
    Assert-Equal $records[1].responseStatus 200 'mutation accepts documented 200 status'
}
finally {
    Stop-Mock $mock
}

$mock = Start-Mock -PrecheckStatus 201 -MutationStatus 202
try {
    $null = Set-VcfAutomationPolicy -Server $mock.Uri -AccessToken 'fixture-token' -TypeId 'com.vmware.policy.deployment.limit'
    $records = Wait-ForRecords -Path $mock.Log -Count 2
    Assert-Equal $records.Count 2 'remaining documented success statuses emit both calls'
    Assert-Equal $records[0].responseStatus 201 'precheck accepts documented 201 status'
    Assert-Equal $records[1].responseStatus 202 'mutation accepts documented 202 status'
}
finally {
    Stop-Mock $mock
}

# A failed validation is a hard gate: the mutation route is never reached.
$mock = Start-Mock -PrecheckStatus 400
try {
    $threw = $false
    try {
        $null = Set-VcfAutomationPolicy -Server $mock.Uri -AccessToken 'fixture-token' -TypeId 'com.vmware.policy.deployment.limit'
    }
    catch {
        $threw = $true
    }
    Assert-True $threw 'failed precheck raises an error'
    $records = Wait-ForRecords -Path $mock.Log -Count 1
    Start-Sleep -Milliseconds 100
    $records = Get-LogRecords -Path $mock.Log
    Assert-Equal $records.Count 1 'failed precheck emits no mutating call'
    Assert-Equal $records[0].target '/policy/api/policies?validationOnly=true' 'failure log contains only precheck target'
    Assert-Equal $records[0].responseStatus 400 'mock returned configured precheck failure'
}
finally {
    Stop-Mock $mock
}

Write-Host "PASS: $script:assertions assertions"
