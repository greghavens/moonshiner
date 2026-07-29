$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $Root (
    'VcfVcenterRoleCollection/VcfVcenterRoleCollection.psd1'
)
$ModulePath = Join-Path $Root (
    'VcfVcenterRoleCollection/VcfVcenterRoleCollection.psm1'
)
$ContractPath = Join-Path $Root 'docs/contract.json'
$SourcesPath = Join-Path $Root 'docs/official_sources.json'
$MockPath = Join-Path $PSScriptRoot 'mock_vcenter.py'
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'vcf91-0091-' + [guid]::NewGuid().ToString('N')
)
[IO.Directory]::CreateDirectory($TempRoot) | Out-Null
$PortPath = Join-Path $TempRoot 'port.txt'
$LogPath = Join-Path $TempRoot 'requests.jsonl'
$ScenarioPath = Join-Path $TempRoot 'scenario.json'
$StdoutPath = Join-Path $TempRoot 'mock.stdout'
$StderrPath = Join-Path $TempRoot 'mock.stderr'
$MockProcess = $null

function Assert-True {
    param(
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
        [AllowNull()]
        $Actual,
        [AllowNull()]
        $Expected,
        [Parameter(Mandatory)]
        [string] $Message
    )
    if ($Actual -cne $Expected) {
        throw "ASSERTION FAILED: $Message (expected '$Expected', got '$Actual')"
    }
}

try {
    $Contract = Get-Content -Raw -LiteralPath $ContractPath |
        ConvertFrom-Json
    $Sources = Get-Content -Raw -LiteralPath $SourcesPath |
        ConvertFrom-Json

    Assert-Equal $Sources.repository 'vmware/vcf-api-specs' `
        'official repository'
    Assert-Equal $Sources.repositoryCommitSha `
        '3949fc33339fc5ea1b77eadb258f1cf49aa88e26' `
        'pinned VCF 9.1 repository commit'
    Assert-Equal $Sources.specPath `
        'specifications/vsphere/openapi/automation/vcenter.yaml' `
        'official specification path'
    Assert-Equal $Sources.specBlobSha `
        '8028b0824c4ff3503d05f44814f967938a795c40' `
        'pinned specification blob'
    Assert-Equal @($Sources.operationIds).Count 1 `
        'official operationId count'
    Assert-Equal $Sources.operationIds[0] `
        'Vcenter.Authorization.Roles_list' 'official operationId'
    Assert-Equal $Sources.operations[0].repositoryCommitSha `
        $Sources.repositoryCommitSha 'operation records repository commit'
    Assert-Equal $Sources.operations[0].specPath $Sources.specPath `
        'operation records specification path'

    Assert-Equal $Contract.source.apiVersion '9.1.0.0' `
        'contract API version'
    Assert-Equal $Contract.source.specPath $Sources.specPath `
        'contract and provenance paths agree'
    Assert-Equal $Contract.source.commitSha `
        $Sources.repositoryCommitSha 'contract and provenance commits agree'
    Assert-Equal @($Contract.operations).Count 1 `
        'focused contract operation count'
    $Operation = $Contract.operations[0]
    Assert-Equal $Operation.operationId `
        'Vcenter.Authorization.Roles_list' 'contract operationId'
    Assert-Equal $Operation.method 'GET' 'contract method'
    Assert-Equal $Operation.path `
        '/api/vcenter/authorization/roles' 'contract API route'
    Assert-Equal (($Operation.effectiveQueryFields.name) -join ',') `
        'is_system,names,privileges,page_size,marker' `
        'contract query-field projection'
    Assert-Equal (
        $Contract.schemas.'Vcenter.Authorization.Roles.IterationSpec'.`
            properties.page_size.defaultWhenMissing
    ) 200 'specification page-size default'
    Assert-Equal (
        $Contract.schemas.'Vcenter.Authorization.Roles.ListResult'.`
            required -join ','
    ) 'items' 'list result required fields'

    $Manifest = Import-PowerShellDataFile -LiteralPath $ManifestPath
    Assert-Equal @($Manifest.RequiredModules).Count 1 `
        'manifest prerequisite count'
    Assert-Equal $Manifest.RequiredModules[0].ModuleName `
        'VMware.Sdk.Vcf.SddcManager' 'VCF PowerCLI module prerequisite'
    Assert-Equal ([version] $Manifest.RequiredModules[0].ModuleVersion) `
        ([version] '13.5.0.25380678') 'VCF PowerCLI module version'
    Assert-Equal (($Manifest.FunctionsToExport) -join ',') `
        'New-VcfVcenterRoleClient,Get-VcfVcenterRoleCollection' `
        'manifest exports'
    Assert-Equal @(
        Get-ChildItem -LiteralPath $Root -Recurse -File |
            Where-Object {
                $_.Extension -in @('.dll', '.nupkg', '.yaml', '.yml')
            }
    ).Count 0 'seed does not vendor modules, assemblies, or OpenAPI files'

    $Tokens = $null
    $ParseErrors = $null
    $Ast = [Management.Automation.Language.Parser]::ParseFile(
        $ModulePath,
        [ref] $Tokens,
        [ref] $ParseErrors
    )
    Assert-Equal @($ParseErrors).Count 0 'module parses without errors'
    $SourceText = Get-Content -Raw -LiteralPath $ModulePath
    foreach ($RequiredText in @(
        'VMware.Sdk.OpenApi.Cmdlets.IServerConnection',
        '.GetClient()',
        'vmware-api-session-id',
        'StringComparer'
    )) {
        Assert-True $SourceText.Contains($RequiredText) `
            "implementation must use $RequiredText"
    }
    foreach ($ForbiddenText in @(
        'Invoke-RestMethod',
        'Invoke-WebRequest',
        'Start-Process',
        'curl',
        'Connect-VIServer'
    )) {
        Assert-True (-not $SourceText.Contains($ForbiddenText)) `
            "implementation must not use $ForbiddenText"
    }

    Import-Module $ManifestPath -Force -ErrorAction Stop
    $Exports = @(
        Get-Command -Module VcfVcenterRoleCollection -CommandType Function |
            Sort-Object Name |
            ForEach-Object Name
    )
    Assert-Equal ($Exports -join ',') `
        'Get-VcfVcenterRoleCollection,New-VcfVcenterRoleClient' `
        'runtime exports'
    $NewCommand = Get-Command New-VcfVcenterRoleClient
    Assert-Equal $NewCommand.Parameters.Connection.ParameterType.FullName `
        'VMware.Sdk.OpenApi.Cmdlets.IServerConnection' `
        'authenticated VCF PowerCLI connection type'
    $GetCommand = Get-Command Get-VcfVcenterRoleCollection
    Assert-Equal $GetCommand.Parameters.PageSize.ParameterType.FullName `
        'System.Int64' 'page-size parameter type'
    $GetFunctionAst = @(
        $Ast.FindAll(
            {
                param($Node)
                $Node -is [Management.Automation.Language.FunctionDefinitionAst] -and
                $Node.Name -ceq 'Get-VcfVcenterRoleCollection'
            },
            $true
        )
    )
    Assert-Equal $GetFunctionAst.Count 1 `
        'one role-collection function definition'
    $PageSizeAst = @(
        $GetFunctionAst[0].Body.ParamBlock.Parameters |
            Where-Object {
                $_.Name.VariablePath.UserPath -ceq 'PageSize'
            }
    )
    Assert-Equal $PageSizeAst.Count 1 'one page-size parameter'
    Assert-Equal $PageSizeAst[0].DefaultValue.Extent.Text '200' `
        'page-size default comes from the specification'

    $RunId = [guid]::NewGuid().ToString('N')
    $SessionToken = 'session-' + $RunId
    $MarkerOne = 'after ' + $RunId.Substring(0, 6) + '/one+?&'
    $MarkerTwo = 'after+' + $RunId.Substring(6, 6) + '/two ?'
    $Roles = @(
        [ordered]@{
            role = 'role-z-' + $RunId.Substring(12, 4)
            info = [ordered]@{
                name = 'zulu'
                description = 'runtime zulu'
                privileges = @('System.Read')
                system = $false
            }
        },
        [ordered]@{
            role = 'role-b-' + $RunId.Substring(16, 4)
            info = [ordered]@{
                name = 'Alpha'
                description = 'runtime alpha b'
                privileges = @('System.Read', 'System.View')
                system = $false
            }
        },
        [ordered]@{
            role = 'role-a-' + $RunId.Substring(20, 4)
            info = [ordered]@{
                name = 'Alpha'
                description = 'runtime alpha a'
                privileges = @('System.Read')
                system = $false
            }
        },
        [ordered]@{
            role = 'role-c-' + $RunId.Substring(24, 4)
            info = [ordered]@{
                name = 'bravo'
                description = 'runtime bravo'
                privileges = @('System.Read')
                system = $true
            }
        },
        [ordered]@{
            role = 'role-d-' + $RunId.Substring(28, 4)
            info = [ordered]@{
                name = 'alpha'
                description = 'runtime lowercase alpha'
                privileges = @('System.Read')
                system = $false
            }
        }
    )
    $Scenario = [ordered]@{
        session_token = $SessionToken
        page_size = 2
        pages = @(
            [ordered]@{
                incoming_marker = $null
                outgoing_marker = $MarkerOne
                items = @($Roles[0], $Roles[3])
            },
            [ordered]@{
                incoming_marker = $MarkerOne
                outgoing_marker = $MarkerTwo
                items = @($Roles[4], $Roles[1])
            },
            [ordered]@{
                incoming_marker = $MarkerTwo
                outgoing_marker = $null
                items = @($Roles[2])
            }
        )
    }
    [IO.File]::WriteAllText(
        $ScenarioPath,
        ($Scenario | ConvertTo-Json -Depth 8 -Compress),
        [Text.UTF8Encoding]::new($false)
    )

    $MockProcess = Start-Process -FilePath 'python3' -ArgumentList @(
        '-B',
        $MockPath,
        $PortPath,
        $LogPath,
        $ContractPath,
        $ScenarioPath
    ) -PassThru -RedirectStandardOutput $StdoutPath `
      -RedirectStandardError $StderrPath

    $Deadline = [Diagnostics.Stopwatch]::StartNew()
    while (-not (Test-Path -LiteralPath $PortPath -PathType Leaf)) {
        if ($MockProcess.HasExited) {
            $Details = Get-Content -Raw -LiteralPath $StderrPath `
                -ErrorAction SilentlyContinue
            throw "loopback mock exited before startup: $Details"
        }
        if ($Deadline.Elapsed.TotalSeconds -gt 10) {
            throw 'timed out waiting for loopback mock startup'
        }
        Start-Sleep -Milliseconds 25
    }
    $Port = [int] (Get-Content -Raw -LiteralPath $PortPath)
    $BaseUrl = "http://127.0.0.1:$Port"

    $Client = New-VcfVcenterRoleClient `
        -Server $BaseUrl `
        -SessionToken $SessionToken
    Assert-True (-not (Test-Path -LiteralPath $LogPath)) `
        'client creation performs no API request'

    $Actual = @(Get-VcfVcenterRoleCollection -Client $Client -PageSize 2)
    Assert-Equal $Actual.Count $Roles.Count `
        'complete collection item count'
    $ExpectedRoleOrder = @(
        $Roles[2].role,
        $Roles[1].role,
        $Roles[4].role,
        $Roles[3].role,
        $Roles[0].role
    )
    Assert-Equal (($Actual | ForEach-Object role) -join ',') `
        ($ExpectedRoleOrder -join ',') `
        'complete collection uses ordinal name and role ordering'
    Assert-Equal (
        ($Actual | ForEach-Object { $_.info.name }) -join ','
    ) 'Alpha,Alpha,alpha,bravo,zulu' `
        'case-sensitive ordinal name ordering'
    $ExpectedRoles = @($Roles | ForEach-Object role | Sort-Object)
    $ActualRoles = @($Actual | ForEach-Object role | Sort-Object)
    Assert-Equal ($ActualRoles -join ',') ($ExpectedRoles -join ',') `
        'every paginated role is returned exactly once'

    $LogLines = @(
        Get-Content -LiteralPath $LogPath |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    Assert-Equal $LogLines.Count 3 'one request per page'
    $Requests = @($LogLines | ForEach-Object { $_ | ConvertFrom-Json })
    $EncodedMarkerOne = [uri]::EscapeDataString($MarkerOne)
    $EncodedMarkerTwo = [uri]::EscapeDataString($MarkerTwo)
    $ExpectedTargets = @(
        '/api/vcenter/authorization/roles?page_size=2',
        (
            '/api/vcenter/authorization/roles?page_size=2&marker=' +
            $EncodedMarkerOne
        ),
        (
            '/api/vcenter/authorization/roles?page_size=2&marker=' +
            $EncodedMarkerTwo
        )
    )
    for ($Index = 0; $Index -lt $Requests.Count; $Index++) {
        $Request = $Requests[$Index]
        Assert-Equal $Request.operationId `
            'Vcenter.Authorization.Roles_list' `
            "request $Index operationId"
        Assert-Equal $Request.method 'GET' "request $Index method"
        Assert-Equal $Request.rawTarget $ExpectedTargets[$Index] `
            "request $Index exact target"
        Assert-Equal $Request.vmwareApiSessionId $SessionToken `
            "request $Index session header"
        Assert-Equal $Request.accept 'application/json' `
            "request $Index accept header"
        Assert-Equal $Request.authorization $null `
            "request $Index omits Authorization"
        Assert-Equal $Request.contentType $null `
            "request $Index omits content type"
        Assert-Equal ([int] $Request.contentLength) 0 `
            "request $Index sends an empty body"
        Assert-Equal $Request.bodyHex '' `
            "request $Index body has zero wire bytes"
        Assert-Equal ([int] $Request.status) 200 `
            "request $Index accepted by contract mock"
        $DecodedQuery = [uri]::UnescapeDataString($Request.rawQuery)
        foreach ($Omitted in @(
            'is_system',
            'names',
            'privileges',
            'filter',
            'iterate'
        )) {
            Assert-True (-not $DecodedQuery.Contains($Omitted)) `
                "request $Index omits optional field $Omitted"
        }
    }
    Assert-True (-not $Requests[0].rawQuery.Contains('marker')) `
        'first request omits marker entirely'
    Assert-True (
        $Requests[1].rawQuery.EndsWith(
            'marker=' + $EncodedMarkerOne,
            [StringComparison]::Ordinal
        )
    ) 'second request preserves and escapes the first marker'
    Assert-True (
        $Requests[2].rawQuery.EndsWith(
            'marker=' + $EncodedMarkerTwo,
            [StringComparison]::Ordinal
        )
    ) 'third request preserves and escapes the second marker'

    'PASS: complete vCenter role pagination, stable order, and exact wire shape'
}
finally {
    if ($null -ne $MockProcess -and -not $MockProcess.HasExited) {
        Stop-Process -Id $MockProcess.Id -Force -ErrorAction SilentlyContinue
        $MockProcess.WaitForExit()
    }
    Remove-Module VcfVcenterRoleCollection -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force `
            -ErrorAction SilentlyContinue
    }
}
