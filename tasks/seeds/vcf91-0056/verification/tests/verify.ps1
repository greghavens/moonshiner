$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $Root 'VcfNsxFailureEvidence/VcfNsxFailureEvidence.psd1'
$ModulePath = Join-Path $Root 'VcfNsxFailureEvidence/VcfNsxFailureEvidence.psm1'
$ContractPath = Join-Path $Root 'docs/contract.json'
$SourcesPath = Join-Path $Root 'docs/official_sources.json'
$MockPath = Join-Path $PSScriptRoot 'mock_nsx_policy.py'
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    'vcf91-0056-' + [guid]::NewGuid().ToString('N')
)
[System.IO.Directory]::CreateDirectory($TempRoot) | Out-Null
$PortPath = Join-Path $TempRoot 'port.txt'
$LogPath = Join-Path $TempRoot 'requests.jsonl'
$ScenarioPath = Join-Path $TempRoot 'scenario.json'
$StdoutPath = Join-Path $TempRoot 'mock.stdout'
$StderrPath = Join-Path $TempRoot 'mock.stderr'
$MockProcess = $null
$HttpClient = $null
$HttpHandler = $null

function Assert-True {
    param(
        [Parameter(Mandatory)]
        [bool] $Condition,

        [Parameter(Mandatory)]
        [string] $Message
    )

    if (-not $Condition) {
        throw "ASSERTION FAILED: $Message"
    }
}

function Assert-Equal {
    param(
        $Actual,
        $Expected,
        [Parameter(Mandatory)]
        [string] $Message
    )

    if ($Actual -ne $Expected) {
        throw "ASSERTION FAILED: $Message (expected '$Expected', got '$Actual')"
    }
}

try {
    Assert-Equal (
        (Get-FileHash -Algorithm SHA256 -LiteralPath $ContractPath).Hash.ToLowerInvariant()
    ) '1c016da5633b8b59e28aa96938c0b0501403694146c17c2e0273263ded81412d' `
        'protected contract hash'
    Assert-Equal (
        (Get-FileHash -Algorithm SHA256 -LiteralPath $SourcesPath).Hash.ToLowerInvariant()
    ) 'fddda442f248fb95f50b8c3af90c6e61d864d8f97ef4a3d334c429fdc60b34d2' `
        'protected official-sources hash'

    $Sources = Get-Content -Raw -LiteralPath $SourcesPath | ConvertFrom-Json
    $Contract = Get-Content -Raw -LiteralPath $ContractPath | ConvertFrom-Json
    $ExpectedCommit = '3949fc33339fc5ea1b77eadb258f1cf49aa88e26'
    $ExpectedSpec = 'specifications/nsx/openapi-2.0/nsx_policy_api.yaml'
    $ExpectedOperationIds = @('ReadIntentStatus', 'ListAlarms')

    Assert-Equal $Sources.repository 'vmware/vcf-api-specs' 'official repository'
    Assert-Equal $Sources.repository_commit_sha $ExpectedCommit `
        'pinned repository commit'
    Assert-Equal $Sources.spec_path $ExpectedSpec 'official specification path'
    Assert-Equal $Sources.spec_blob_sha `
        '102d15fd342f6a45bb6d84a5b39a916c65929f4c' 'pinned spec blob'
    Assert-Equal @($Sources.operationIds).Count 2 'official operationId count'
    Assert-Equal (@($Sources.operationIds) -join '|') `
        ($ExpectedOperationIds -join '|') 'official operationIds'
    Assert-Equal @($Sources.operations).Count 2 'official operation records'
    foreach ($Index in 0..1) {
        Assert-Equal $Sources.operations[$Index].operationId `
            $ExpectedOperationIds[$Index] "official operation $Index id"
        Assert-Equal $Sources.operations[$Index].repository_commit_sha `
            $ExpectedCommit "official operation $Index commit"
        Assert-Equal $Sources.operations[$Index].spec_path `
            $ExpectedSpec "official operation $Index spec path"
    }

    Assert-Equal $Contract.source.repository_commit_sha $ExpectedCommit `
        'contract source commit'
    Assert-Equal $Contract.source.spec_path $ExpectedSpec 'contract source path'
    Assert-Equal $Contract.basePath '/policy/api/v1' 'contract base path'
    Assert-Equal @($Contract.operations).Count 2 'contract operation count'
    Assert-Equal (@($Contract.operations.operationId) -join '|') `
        ($ExpectedOperationIds -join '|') 'contract operationIds'
    Assert-Equal $Contract.operations[0].path `
        '/policy/api/v1/infra/realized-state/status' 'status contract route'
    Assert-Equal $Contract.operations[1].path `
        '/policy/api/v1/infra/realized-state/alarms' 'alarms contract route'
    Assert-Equal (@($Contract.operations[0].parameters.name) -join '|') `
        'include_enforced_status|intent_path|site_path' `
        'status contract parameter projection'
    Assert-Equal (@($Contract.operations[1].parameters.name) -join '|') `
        'cursor|included_fields|page_size|sort_ascending|sort_by' `
        'alarms contract parameter projection'

    $Manifest = Import-PowerShellDataFile -LiteralPath $ManifestPath
    Assert-Equal @($Manifest.FunctionsToExport).Count 1 'manifest export count'
    Assert-Equal $Manifest.FunctionsToExport[0] `
        'Get-VcfNsxIntentFailureEvidence' 'manifest export'
    Assert-Equal @($Manifest.RequiredModules).Count 1 'required module count'
    Assert-Equal $Manifest.RequiredModules[0].ModuleName `
        'VMware.Sdk.Vcf.SddcManager' 'VCF SDK prerequisite'
    Assert-Equal ([version] $Manifest.RequiredModules[0].ModuleVersion) `
        ([version] '13.5.0.25380678') 'VCF SDK prerequisite version'

    $SourceText = Get-Content -Raw -LiteralPath $ModulePath
    foreach ($Forbidden in @(
        'Invoke-RestMethod',
        'Invoke-WebRequest',
        'System.Net.Http',
        'HttpClient',
        'curl',
        'Start-Process'
    )) {
        Assert-True (-not $SourceText.Contains($Forbidden)) `
            "production module must not contain $Forbidden"
    }
    Assert-True $SourceText.Contains('ReadIntentStatus') `
        'production module must use the generated ReadIntentStatus binding'
    Assert-True $SourceText.Contains('ListAlarms') `
        'production module must use the generated ListAlarms binding'

    $Sdk = Get-Module -ListAvailable -Name VMware.Sdk.Vcf.SddcManager |
        Where-Object Version -GE ([version] '13.5.0.25380678') |
        Sort-Object Version -Descending |
        Select-Object -First 1
    Assert-True ($null -ne $Sdk) 'VCF PowerCLI 9.1 prerequisite is installed'
    Import-Module $Sdk.Path -ErrorAction Stop
    Import-Module $ManifestPath -Force -ErrorAction Stop

    $Exports = @((Get-Command -Module VcfNsxFailureEvidence).Name)
    Assert-Equal $Exports.Count 1 'runtime export count'
    Assert-Equal $Exports[0] 'Get-VcfNsxIntentFailureEvidence' 'runtime export'

    $RunId = [guid]::NewGuid().ToString('N')
    $Token = 'fixture-token-' + $RunId
    $IntentPath = (
        '/infra/domains/default/security-policies/policy ' +
        $RunId.Substring(0, 8) +
        '/failed?'
    )
    $Cursor = 'next+' + $RunId.Substring(8, 8) + '/alarm page?'
    $UnrelatedId = 'alarm-unrelated-' + $RunId.Substring(16, 4)
    $OlderId = 'alarm-older-' + $RunId.Substring(20, 4)
    $SubstringId = 'alarm-prefix-' + $RunId.Substring(24, 4)
    $NewestId = 'alarm-newest-' + $RunId.Substring(28, 4)
    $UnrelatedMessage = 'unrelated provider fault "' + $RunId.Substring(0, 5) + '"'
    $OlderMessage = 'earlier matching event \' + $RunId.Substring(5, 5)
    $NewestMessage = 'evidence: edge publish failed "' + $RunId.Substring(10, 5) + '"'

    $Scenario = [ordered] @{
        token = $Token
        intent_path = $IntentPath
        page_size = 2
        status = [ordered] @{
            intent_path = $IntentPath
            publish_status = 'ERROR'
            consolidated_status = [ordered] @{
                consolidated_status = 'ERROR'
            }
        }
        pages = @(
            [ordered] @{
                incoming_cursor = $null
                outgoing_cursor = $Cursor
                results = @(
                    [ordered] @{
                        resource_type = 'PolicyAlarmResource'
                        id = $UnrelatedId
                        display_name = 'unrelated'
                        intent_paths = @($IntentPath.ToUpperInvariant())
                        message = $UnrelatedMessage
                        severity = 'ERROR'
                        source_reference = $IntentPath
                        _create_time = 1900000000000
                    },
                    [ordered] @{
                        resource_type = 'PolicyAlarmResource'
                        id = $OlderId
                        display_name = 'matching-older'
                        intent_paths = @(
                            $IntentPath,
                            '/infra/domains/default/groups/shared'
                        )
                        message = $OlderMessage
                        severity = 'WARNING'
                        source_reference = '/infra/realized-state/matching-older'
                        _create_time = 1800000000000
                    }
                )
            },
            [ordered] @{
                incoming_cursor = $Cursor
                outgoing_cursor = $null
                results = @(
                    [ordered] @{
                        resource_type = 'PolicyAlarmResource'
                        id = $SubstringId
                        display_name = 'prefix-only'
                        intent_paths = @($IntentPath + '/child')
                        message = 'prefix-only event ' + $RunId.Substring(15, 5)
                        severity = 'ERROR'
                        source_reference = '/infra/realized-state/prefix-only'
                        _create_time = 1950000000000
                    },
                    [ordered] @{
                        resource_type = 'PolicyAlarmResource'
                        id = $NewestId
                        display_name = 'matching-newest'
                        intent_paths = @($IntentPath)
                        message = $NewestMessage
                        severity = 'ERROR'
                        source_reference = '/infra/realized-state/matching-newest'
                        _create_time = 1850000000000
                    }
                )
            }
        )
    }
    [System.IO.File]::WriteAllText(
        $ScenarioPath,
        ($Scenario | ConvertTo-Json -Depth 10 -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )

    $MockProcess = Start-Process -FilePath 'python3' -ArgumentList @(
        '-B',
        $MockPath,
        $PortPath,
        $LogPath,
        $ContractPath,
        $ScenarioPath
    ) -PassThru -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath

    $Deadline = [System.Diagnostics.Stopwatch]::StartNew()
    while (-not (Test-Path -LiteralPath $PortPath)) {
        if ($MockProcess.HasExited) {
            $MockError = Get-Content -Raw -LiteralPath $StderrPath
            throw "Loopback mock exited before startup: $MockError"
        }
        if ($Deadline.Elapsed.TotalSeconds -gt 10) {
            throw 'Timed out waiting for loopback mock startup.'
        }
        Start-Sleep -Milliseconds 25
    }
    $Port = [int] (Get-Content -Raw -LiteralPath $PortPath)

    $Configuration = [VMware.Binding.OpenApi.Client.Configuration]::new()
    $Configuration.BasePath = "http://127.0.0.1:$Port/policy/api/v1"
    $Configuration.AccessToken = $Token
    $HttpHandler = [System.Net.Http.HttpClientHandler]::new()
    $HttpClient = [System.Net.Http.HttpClient]::new($HttpHandler, $false)
    $RealizedStateApi = [VMware.Bindings.Nsx.Policy.Api.PolicyRealizedStateApi]::new(
        $HttpClient,
        $Configuration,
        $HttpHandler
    )

    $Report = Get-VcfNsxIntentFailureEvidence `
        -RealizedStateApi $RealizedStateApi `
        -IntentPath $IntentPath `
        -PageSize 2

    Assert-Equal (
        @($Report.PSObject.Properties.Name) -join '|'
    ) (
        'IntentPath|IntentStatus|RelevantAlarms|EvidenceMessage|' +
        'ScannedAlarmCount|AlarmPageCount'
    ) 'report property order'
    Assert-Equal $Report.IntentPath $IntentPath 'report intent path'
    Assert-True (
        $Report.IntentStatus -is
        [VMware.Bindings.Nsx.Policy.Model.ConsolidatedRealizedStatus]
    ) 'report preserves generated status type'
    Assert-Equal $Report.IntentStatus.IntentPath $IntentPath `
        'generated status has requested intent path'
    Assert-Equal @($Report.RelevantAlarms).Count 2 'only exact-path alarms match'
    foreach ($Alarm in @($Report.RelevantAlarms)) {
        Assert-True (
            $Alarm -is [VMware.Bindings.Nsx.Policy.Model.PolicyAlarmResource]
        ) 'report preserves generated alarm type'
    }
    Assert-Equal (
        @($Report.RelevantAlarms.Id) -join '|'
    ) "$NewestId|$OlderId" 'relevant alarms use deterministic newest-first order'
    Assert-Equal $Report.EvidenceMessage $NewestMessage `
        'evidence message comes from newest relevant event'
    Assert-True ($Report.EvidenceMessage -ne $UnrelatedMessage) `
        'unrelated event is not used as diagnosis'
    Assert-Equal $Report.ScannedAlarmCount 4 'all alarms are counted'
    Assert-Equal $Report.AlarmPageCount 2 'all alarm pages are counted'

    Start-Sleep -Milliseconds 50
    $Requests = @(
        Get-Content -LiteralPath $LogPath |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
    Assert-Equal $Requests.Count 3 'exactly one status and two alarm requests'
    Assert-Equal (@($Requests.operationId) -join '|') `
        'ReadIntentStatus|ListAlarms|ListAlarms' 'exact operation order'

    $ExpectedAuth = "Bearer $Token"
    $EncodedIntentPath = [uri]::EscapeDataString($IntentPath)
    $EncodedCursor = [uri]::EscapeDataString($Cursor)
    $ExpectedPaths = @(
        '/policy/api/v1/infra/realized-state/status',
        '/policy/api/v1/infra/realized-state/alarms',
        '/policy/api/v1/infra/realized-state/alarms'
    )
    $ExpectedQueries = @(
        "include_enforced_status=true&intent_path=$EncodedIntentPath",
        'page_size=2',
        "cursor=$EncodedCursor&page_size=2"
    )

    for ($Index = 0; $Index -lt $Requests.Count; $Index++) {
        $Request = $Requests[$Index]
        Assert-Equal $Request.method 'GET' "request $Index method"
        Assert-Equal $Request.path $ExpectedPaths[$Index] "request $Index path"
        Assert-Equal $Request.rawQuery $ExpectedQueries[$Index] `
            "request $Index exact raw query"
        Assert-Equal $Request.authorization $ExpectedAuth `
            "request $Index bearer authorization"
        Assert-True ($Request.accept -like 'application/json*') `
            "request $Index Accept header"
        Assert-True ($null -eq $Request.contentType) `
            "request $Index omits Content-Type"
        Assert-Equal $Request.contentLength 0 "request $Index empty body length"
        Assert-Equal $Request.body '' "request $Index empty body"
    }

    $StatusQuery = $Requests[0].query
    Assert-Equal @($StatusQuery.intent_path).Count 1 `
        'status query has one intent_path'
    Assert-Equal $StatusQuery.intent_path[0] $IntentPath `
        'status query intent path value'
    Assert-Equal $StatusQuery.include_enforced_status[0] 'true' `
        'status query requests enforced details'
    Assert-True (
        $StatusQuery.PSObject.Properties.Name -notcontains 'site_path'
    ) 'status query omits optional site_path'

    for ($Index = 1; $Index -le 2; $Index++) {
        $AlarmQuery = $Requests[$Index].query
        Assert-Equal @($AlarmQuery.page_size).Count 1 `
            "alarm request $Index has one page_size"
        Assert-Equal $AlarmQuery.page_size[0] '2' `
            "alarm request $Index page_size"
        foreach ($Name in @('included_fields', 'sort_ascending', 'sort_by')) {
            Assert-True (
                $AlarmQuery.PSObject.Properties.Name -notcontains $Name
            ) "alarm request $Index omits optional $Name"
        }
    }
    Assert-True (
        $Requests[1].query.PSObject.Properties.Name -notcontains 'cursor'
    ) 'first alarm request omits cursor'
    Assert-Equal @($Requests[2].query.cursor).Count 1 `
        'second alarm request has one cursor'
    Assert-Equal $Requests[2].query.cursor[0] $Cursor `
        'second alarm request follows the opaque cursor'

    Write-Output 'all checks passed'
}
finally {
    if ($null -ne $HttpClient) {
        $HttpClient.Dispose()
    }
    if ($null -ne $HttpHandler) {
        $HttpHandler.Dispose()
    }
    if ($null -ne $MockProcess -and -not $MockProcess.HasExited) {
        Stop-Process -Id $MockProcess.Id -Force -ErrorAction SilentlyContinue
        $MockProcess.WaitForExit()
    }
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
}
