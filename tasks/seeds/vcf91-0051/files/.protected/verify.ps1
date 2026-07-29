$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $Root 'VcfNsxGroupInventory/VcfNsxGroupInventory.psd1'
$ModulePath = Join-Path $Root 'VcfNsxGroupInventory/VcfNsxGroupInventory.psm1'
$ContractPath = Join-Path $Root 'docs/contract.json'
$SourcesPath = Join-Path $Root 'docs/official_sources.json'
$MockPath = Join-Path $PSScriptRoot 'mock_nsx_policy.py'
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    'vcf91-0051-' + [guid]::NewGuid().ToString('N')
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
    $Sources = Get-Content -Raw -LiteralPath $SourcesPath | ConvertFrom-Json
    $Contract = Get-Content -Raw -LiteralPath $ContractPath | ConvertFrom-Json
    Assert-Equal $Sources.repository 'vmware/vcf-api-specs' 'official repository'
    Assert-Equal $Sources.repository_commit_sha `
        '3949fc33339fc5ea1b77eadb258f1cf49aa88e26' 'pinned repository commit'
    Assert-Equal $Sources.spec_path `
        'specifications/nsx/openapi-2.0/nsx_policy_api.yaml' 'official spec path'
    Assert-Equal @($Sources.operationIds).Count 1 'official operationId count'
    Assert-Equal $Sources.operationIds[0] 'ListGroupForDomain' 'official operationId'
    Assert-Equal @($Contract.operations).Count 1 'contract operation count'
    Assert-Equal $Contract.operations[0].operationId `
        'ListGroupForDomain' 'contract operationId'
    Assert-Equal $Contract.operations[0].path `
        '/policy/api/v1/infra/domains/{domain-id}/groups' 'contract route'

    $Manifest = Import-PowerShellDataFile -LiteralPath $ManifestPath
    Assert-Equal @($Manifest.FunctionsToExport).Count 1 'manifest export count'
    Assert-Equal $Manifest.FunctionsToExport[0] `
        'Get-VcfNsxPolicyGroupInventory' 'manifest export'
    Assert-Equal @($Manifest.RequiredModules).Count 1 'required module count'
    Assert-Equal $Manifest.RequiredModules[0].ModuleName `
        'VMware.Sdk.Vcf.SddcManager' 'VCF SDK prerequisite'
    Assert-Equal ([version]$Manifest.RequiredModules[0].ModuleVersion) `
        ([version]'13.5.0.25380678') 'VCF SDK prerequisite version'

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
    Assert-True $SourceText.Contains('ListGroupForDomain') `
        'production module must use the generated ListGroupForDomain binding'

    $Sdk = Get-Module -ListAvailable -Name VMware.Sdk.Vcf.SddcManager |
        Where-Object Version -GE ([version]'13.5.0.25380678') |
        Sort-Object Version -Descending |
        Select-Object -First 1
    Assert-True ($null -ne $Sdk) 'VCF PowerCLI 9.1 prerequisite is installed'
    Import-Module $Sdk.Path -ErrorAction Stop
    Import-Module $ManifestPath -Force -ErrorAction Stop

    $Exports = @((Get-Command -Module VcfNsxGroupInventory).Name)
    Assert-Equal $Exports.Count 1 'runtime export count'
    Assert-Equal $Exports[0] 'Get-VcfNsxPolicyGroupInventory' 'runtime export'

    $RunId = [guid]::NewGuid().ToString('N')
    $Username = 'fixture-user-' + $RunId.Substring(0, 8)
    $Password = 'fixture-pass-' + $RunId.Substring(8, 8)
    $DomainId = 'domain ' + $RunId.Substring(16, 6) + '/blue?'
    $CursorOne = 'next ' + $RunId.Substring(22, 5) + '/one'
    $CursorTwo = 'next+' + $RunId.Substring(27, 5) + '?two'
    $GroupData = @(
        [ordered]@{
            id = 'g-z-' + $RunId.Substring(0, 4)
            display_name = 'alpha'
            path = '/infra/domains/runtime/groups/g-z'
            resource_type = 'Group'
        },
        [ordered]@{
            id = 'g-b-' + $RunId.Substring(4, 4)
            display_name = 'Beta'
            path = '/infra/domains/runtime/groups/g-b'
            resource_type = 'Group'
        },
        [ordered]@{
            id = 'g-a-' + $RunId.Substring(8, 4)
            display_name = 'Alpha'
            path = '/infra/domains/runtime/groups/g-a'
            resource_type = 'Group'
        },
        [ordered]@{
            id = 'g-c-' + $RunId.Substring(12, 4)
            display_name = 'alpha'
            path = '/infra/domains/runtime/groups/g-c'
            resource_type = 'Group'
        },
        [ordered]@{
            id = 'g-d-' + $RunId.Substring(16, 4)
            display_name = 'zulu'
            path = '/infra/domains/runtime/groups/g-d'
            resource_type = 'Group'
        }
    )
    $Scenario = [ordered]@{
        username = $Username
        password = $Password
        domain_id = $DomainId
        page_size = 2
        pages = @(
            [ordered]@{
                incoming_cursor = $null
                outgoing_cursor = $CursorOne
                results = @($GroupData[4], $GroupData[1])
            },
            [ordered]@{
                incoming_cursor = $CursorOne
                outgoing_cursor = $CursorTwo
                results = @($GroupData[0], $GroupData[2])
            },
            [ordered]@{
                incoming_cursor = $CursorTwo
                outgoing_cursor = $null
                results = @($GroupData[3])
            }
        )
    }
    [System.IO.File]::WriteAllText(
        $ScenarioPath,
        ($Scenario | ConvertTo-Json -Depth 8 -Compress),
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
    $Port = [int](Get-Content -Raw -LiteralPath $PortPath)

    $Configuration = [VMware.Binding.OpenApi.Client.Configuration]::new()
    $Configuration.BasePath = "http://127.0.0.1:$Port/policy/api/v1"
    $Configuration.Username = $Username
    $Configuration.Password = ConvertTo-SecureString $Password -AsPlainText -Force
    $HttpHandler = [System.Net.Http.HttpClientHandler]::new()
    $HttpClient = [System.Net.Http.HttpClient]::new($HttpHandler, $false)
    $PolicyApi = [VMware.Bindings.Nsx.Policy.Api.PolicyApi]::new(
        $HttpClient,
        $Configuration,
        $HttpHandler
    )

    $Actual = @(
        Get-VcfNsxPolicyGroupInventory `
            -PolicyApi $PolicyApi `
            -DomainId $DomainId `
            -PageSize 2
    )
    Assert-Equal $Actual.Count 5 'all pages are emitted'
    foreach ($Item in $Actual) {
        Assert-True (
            $Item -is [VMware.Bindings.Nsx.Policy.Model.Group]
        ) 'results retain the generated Group type'
    }
    $ExpectedIds = @(
        $GroupData[2].id,
        $GroupData[3].id,
        $GroupData[0].id,
        $GroupData[1].id,
        $GroupData[4].id
    )
    Assert-Equal (($Actual.Id) -join '|') ($ExpectedIds -join '|') `
        'complete collection uses stable ordinal ordering and tie-breakers'

    Start-Sleep -Milliseconds 50
    $Requests = @(
        Get-Content -LiteralPath $LogPath |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
    Assert-Equal $Requests.Count 3 'exactly three collection pages requested'

    $EncodedDomain = [uri]::EscapeDataString($DomainId)
    $ExpectedPath = "/policy/api/v1/infra/domains/$EncodedDomain/groups"
    $ExpectedAuth = 'Basic ' + [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes("${Username}:${Password}")
    )
    $ExpectedQueries = @(
        'page_size=2',
        ('cursor=' + [uri]::EscapeDataString($CursorOne) + '&page_size=2'),
        ('cursor=' + [uri]::EscapeDataString($CursorTwo) + '&page_size=2')
    )
    $ExpectedCursors = @($null, $CursorOne, $CursorTwo)
    $ForbiddenQuery = @(
        'include_mark_for_delete_objects',
        'included_fields',
        'member_types',
        'sort_ascending',
        'sort_by'
    )

    for ($Index = 0; $Index -lt $Requests.Count; $Index++) {
        $Request = $Requests[$Index]
        Assert-Equal $Request.operationId 'ListGroupForDomain' `
            "request $Index operation"
        Assert-Equal $Request.method 'GET' "request $Index method"
        Assert-Equal $Request.path $ExpectedPath "request $Index escaped path"
        Assert-Equal $Request.rawQuery $ExpectedQueries[$Index] `
            "request $Index exact raw query"
        Assert-Equal $Request.authorization $ExpectedAuth `
            "request $Index authorization"
        Assert-True ($Request.accept -like 'application/json*') `
            "request $Index Accept header"
        Assert-True ($null -eq $Request.contentType) `
            "request $Index omits Content-Type"
        Assert-Equal $Request.contentLength 0 "request $Index empty body length"
        Assert-Equal $Request.body '' "request $Index empty body"
        Assert-Equal @($Request.query.page_size).Count 1 `
            "request $Index has one page_size"
        Assert-Equal $Request.query.page_size[0] '2' `
            "request $Index page_size value"
        foreach ($Name in $ForbiddenQuery) {
            Assert-True (
                $Request.query.PSObject.Properties.Name -notcontains $Name
            ) "request $Index omits optional $Name"
        }
        if ($null -eq $ExpectedCursors[$Index]) {
            Assert-True (
                $Request.query.PSObject.Properties.Name -notcontains 'cursor'
            ) 'first request omits cursor'
        }
        else {
            Assert-Equal @($Request.query.cursor).Count 1 `
                "request $Index has one cursor"
            Assert-Equal $Request.query.cursor[0] $ExpectedCursors[$Index] `
                "request $Index cursor value"
        }
    }

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
