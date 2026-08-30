$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-True {
    param(
        [Parameter(Mandatory)]
        [bool] $Condition,

        [Parameter(Mandatory)]
        [string] $Message
    )

    if (-not $Condition) {
        throw "Assertion failed: $Message"
    }
}

function Assert-Equal {
    param(
        [AllowNull()]
        [object] $Actual,

        [AllowNull()]
        [object] $Expected,

        [Parameter(Mandatory)]
        [string] $Message
    )

    if ($Actual -cne $Expected) {
        throw "Assertion failed: $Message (expected '$Expected', got '$Actual')"
    }
}

function Get-QueryMap {
    param([Parameter(Mandatory)] [object] $Request)

    $map = @{}
    foreach ($pair in @($Request.query)) {
        $name = [string] $pair[0]
        Assert-True (-not $map.ContainsKey($name)) "query key '$name' must occur once"
        $map[$name] = [string] $pair[1]
    }
    return $map
}

function Assert-IncidentFields {
    param(
        [Parameter(Mandatory)] [object[]] $Actual,
        [Parameter(Mandatory)] [hashtable] $Expected
    )

    foreach ($incident in $Actual) {
        $entityId = [string] $incident.entity_id
        Assert-True ($Expected.ContainsKey($entityId)) "unexpected incident '$entityId'"
        $expectedFields = $Expected[$entityId]
        foreach ($field in @('start_entity_id', 'name', 'status')) {
            $property = $incident.PSObject.Properties[$field]
            Assert-True ($null -ne $property) "incident '$entityId' is missing $field"
            Assert-Equal ([string] $property.Value) ([string] $expectedFields[$field]) "incident '$entityId' has incorrect $field"
        }
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$contractPath = Join-Path $repoRoot 'docs/contract.json'
$sourcesPath = Join-Path $repoRoot 'docs/official_sources.json'
$manifestPath = Join-Path $repoRoot 'Vcf.Operations.Networks/Vcf.Operations.Networks.psd1'
$modulePath = Join-Path $repoRoot 'Vcf.Operations.Networks/Vcf.Operations.Networks.psm1'

$contract = Get-Content -Raw $contractPath | ConvertFrom-Json -Depth 30
$sources = Get-Content -Raw $sourcesPath | ConvertFrom-Json -Depth 10
Assert-Equal $contract.api_version '9.0.0.0' 'contract API version must stay pinned'
Assert-Equal $contract.server_base_path '/api/ni' 'contract server base path must stay pinned'
Assert-Equal @($contract.operations).Count 1 'contract must name one operation'
Assert-Equal $contract.operations[0].operationId 'listTroubleshootingIncidents' 'operationId must match the specification'
Assert-Equal $contract.operations[0].method 'GET' 'operation method must match the specification'
Assert-Equal $contract.operations[0].path '/gnt/troubleshoot/incidents' 'operation path must match the specification'
Assert-Equal $contract.operations[0].wire_path '/api/ni/gnt/troubleshoot/incidents' 'wire path must include the specified server prefix'
Assert-Equal $sources.revision.tag '9.0.0.0' 'official source tag must stay pinned'
Assert-Equal $sources.revision.commit_sha '85151f6b1bb58f13b6ac0304bfec53904bea085f' 'official source commit must stay pinned'
Assert-Equal $sources.spec_path 'specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml' 'official spec path must stay pinned'
Assert-Equal @($sources.operations).Count 1 'official sources must record each operationId'
Assert-Equal $sources.operations[0].operationId 'listTroubleshootingIncidents' 'official source operationId must match the contract'

Assert-True (Test-Path -LiteralPath $manifestPath -PathType Leaf) 'module manifest is missing'
Assert-True (Test-Path -LiteralPath $modulePath -PathType Leaf) 'module implementation is missing'
$manifest = Test-ModuleManifest -Path $manifestPath
Assert-Equal $manifest.RootModule 'Vcf.Operations.Networks.psm1' 'manifest RootModule is incorrect'
Assert-True (@($manifest.ExportedFunctions.Keys) -ccontains 'Get-VcfOperationsNetworkTroubleshootingIncident') 'required function is not exported'
$requiredSdk = @($manifest.RequiredModules) | Where-Object { $_.Name -ceq 'VMware.Sdk.Vcf.Ops' }
Assert-Equal @($requiredSdk).Count 1 'manifest must require VMware.Sdk.Vcf.Ops exactly once'
Assert-Equal $requiredSdk[0].Version.ToString() '13.5.0.25380678' 'manifest must target the installed VMware.Sdk.Vcf.Ops version'

$vendored = @(Get-ChildItem -LiteralPath (Split-Path -Parent $manifestPath) -Recurse -File | Where-Object {
        $_.Extension -in @('.dll', '.nupkg') -or $_.Name -like 'VMware.Sdk.Vcf*'
    })
Assert-Equal $vendored.Count 0 'the implementation must not vendor VMware SDK artifacts'

Import-Module $manifestPath -Force
$command = Get-Command Get-VcfOperationsNetworkTroubleshootingIncident -ErrorAction Stop
Assert-Equal $command.Parameters['ServerUri'].ParameterType.FullName 'System.Uri' 'ServerUri type is incorrect'
Assert-Equal $command.Parameters['ApiToken'].ParameterType.FullName 'System.String' 'ApiToken type is incorrect'
Assert-Equal $command.Parameters['PageSize'].ParameterType.FullName 'System.Double' 'PageSize type is incorrect'
Assert-Equal $command.Parameters['StartEntityId'].ParameterType.FullName 'System.String' 'StartEntityId type is incorrect'
$mandatoryServerUri = @($command.Parameters['ServerUri'].Attributes | Where-Object {
        $_ -is [Management.Automation.ParameterAttribute] -and $_.Mandatory
    })
$mandatoryApiToken = @($command.Parameters['ApiToken'].Attributes | Where-Object {
        $_ -is [Management.Automation.ParameterAttribute] -and $_.Mandatory
    })
$mandatoryPageSize = @($command.Parameters['PageSize'].Attributes | Where-Object {
        $_ -is [Management.Automation.ParameterAttribute] -and $_.Mandatory
    })
$mandatoryStartEntityId = @($command.Parameters['StartEntityId'].Attributes | Where-Object {
        $_ -is [Management.Automation.ParameterAttribute] -and $_.Mandatory
    })
Assert-True ($mandatoryServerUri.Count -gt 0) 'ServerUri must be mandatory'
Assert-True ($mandatoryApiToken.Count -gt 0) 'ApiToken must be mandatory'
Assert-Equal $mandatoryPageSize.Count 0 'PageSize must be optional'
Assert-Equal $mandatoryStartEntityId.Count 0 'StartEntityId must be optional'

$runId = [guid]::NewGuid().ToString('N')
$logPath = Join-Path ([IO.Path]::GetTempPath()) "vcf-networks-$runId.jsonl"
$readyPath = Join-Path ([IO.Path]::GetTempPath()) "vcf-networks-$runId.ready"
$stdoutPath = Join-Path ([IO.Path]::GetTempPath()) "vcf-networks-$runId.stdout"
$stderrPath = Join-Path ([IO.Path]::GetTempPath()) "vcf-networks-$runId.stderr"
$serverPath = Join-Path $repoRoot 'mock/server.py'
$server = $null

try {
    $server = Start-Process -FilePath 'python3' -ArgumentList @(
        $serverPath,
        '--contract', $contractPath,
        '--log', $logPath,
        '--ready', $readyPath
    ) -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path -LiteralPath $readyPath)) {
        if ($server.HasExited) {
            $detail = if (Test-Path $stderrPath) { Get-Content -Raw $stderrPath } else { '' }
            throw "mock exited before becoming ready: $detail"
        }
        if ([DateTime]::UtcNow -gt $deadline) {
            throw 'mock did not become ready'
        }
        Start-Sleep -Milliseconds 40
    }

    $port = [int] (Get-Content -Raw $readyPath)
    $serverUri = [uri] "http://127.0.0.1:$port"
    $expectedIncidentFields = @{
        'incident-Charlie' = @{ start_entity_id = 'vm-103'; name = 'Charlie path'; status = 'CLOSED' }
        'incident-Echo' = @{ start_entity_id = 'vm-105'; name = 'Echo path'; status = 'OPEN' }
        'incident-alpha' = @{ start_entity_id = 'vm-101'; name = 'Alpha path'; status = 'CLOSED' }
        'incident-bravo' = @{ start_entity_id = 'vm-102'; name = 'Bravo path'; status = 'CLOSED' }
        'incident-delta' = @{ start_entity_id = 'vm-104'; name = 'Delta path'; status = 'OPEN' }
        'incident-zeta' = @{ start_entity_id = 'vm-107'; name = 'Zeta path'; status = 'OPEN' }
    }
    $expectedIds = @('incident-Charlie', 'incident-Echo', 'incident-alpha', 'incident-bravo', 'incident-delta', 'incident-zeta')

    $withoutOptionals = @(Get-VcfOperationsNetworkTroubleshootingIncident `
            -ServerUri $serverUri -ApiToken 'wire-token')
    Assert-Equal $withoutOptionals.Count 6 'all pages must be returned without optional inputs'
    Assert-Equal (($withoutOptionals.entity_id) -join ',') ($expectedIds -join ',') 'output must be sorted by entity_id'
    Assert-IncidentFields -Actual $withoutOptionals -Expected $expectedIncidentFields

    $withOptionals = @(Get-VcfOperationsNetworkTroubleshootingIncident `
            -ServerUri $serverUri -ApiToken 'wire-token' `
            -PageSize 2 -StartEntityId 'entity root/7')
    Assert-Equal $withOptionals.Count 6 'all pages must be returned with optional inputs'
    Assert-Equal (($withOptionals.entity_id) -join ',') ($expectedIds -join ',') 'filtered output must be sorted by entity_id'
    Assert-IncidentFields -Actual $withOptionals -Expected $expectedIncidentFields

    $requests = @(
        Get-Content $logPath | Where-Object { $_.Trim() } | ForEach-Object {
            $_ | ConvertFrom-Json -Depth 10
        }
    )
    Assert-Equal $requests.Count 6 'each invocation must fetch exactly three pages'

    for ($index = 0; $index -lt $requests.Count; $index++) {
        $request = $requests[$index]
        Assert-Equal $request.operationId 'listTroubleshootingIncidents' "request $index operationId is incorrect"
        Assert-Equal $request.method 'GET' "request $index method is incorrect"
        Assert-Equal $request.path '/api/ni/gnt/troubleshoot/incidents' "request $index path is incorrect"
        Assert-Equal @($request.headers.authorization).Count 1 "request $index must send one Authorization header"
        Assert-Equal $request.headers.authorization[0] 'NetworkInsight wire-token' "request $index authorization is incorrect"
        Assert-Equal ([int] $request.content_length) 0 "request $index must not send a body"
        Assert-Equal $request.body '' "request $index body must be empty"
    }

    $firstQuery = Get-QueryMap $requests[0]
    Assert-Equal $firstQuery.Count 0 'unset size, cursor, and start_entity_id must all be omitted on the first request'

    $secondQuery = Get-QueryMap $requests[1]
    Assert-Equal $secondQuery.Count 1 'the second request must contain only cursor when optionals are unset'
    Assert-Equal $secondQuery.cursor 'page-2' 'the second request cursor is incorrect'
    Assert-True (-not $secondQuery.ContainsKey('size')) 'unset size must be omitted on subsequent pages'
    Assert-True (-not $secondQuery.ContainsKey('start_entity_id')) 'unset start_entity_id must be omitted on subsequent pages'

    $thirdQuery = Get-QueryMap $requests[2]
    Assert-Equal $thirdQuery.Count 1 'the third request must contain only cursor when optionals are unset'
    Assert-Equal $thirdQuery.cursor 'page+3/=' 'the third request cursor is incorrectly encoded or decoded'

    $expectedCursors = @($null, 'page-2', 'page+3/=')
    for ($offset = 0; $offset -lt 3; $offset++) {
        $query = Get-QueryMap $requests[$offset + 3]
        $expectedCount = if ($offset -eq 0) { 2 } else { 3 }
        Assert-Equal $query.Count $expectedCount "configured request $offset has the wrong query field count"
        $parsedSize = [double]::Parse($query.size, [Globalization.CultureInfo]::InvariantCulture)
        Assert-Equal $parsedSize ([double] 2) "configured request $offset must preserve size"
        Assert-Equal $query.start_entity_id 'entity root/7' "configured request $offset must preserve start_entity_id"
        if ($null -eq $expectedCursors[$offset]) {
            Assert-True (-not $query.ContainsKey('cursor')) 'the first configured request must omit cursor'
        }
        else {
            Assert-Equal $query.cursor $expectedCursors[$offset] "configured request $offset cursor is incorrect"
        }
    }

    Write-Host 'Verification passed.'
}
finally {
    if ($null -ne $server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
        $server.WaitForExit()
    }
    Remove-Item -LiteralPath $logPath, $readyPath, $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
}
