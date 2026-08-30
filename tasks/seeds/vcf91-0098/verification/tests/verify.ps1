$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $Root (
    'VcfVcenterPrivilegeInventory/VcfVcenterPrivilegeInventory.psd1'
)
$ModulePath = Join-Path $Root (
    'VcfVcenterPrivilegeInventory/VcfVcenterPrivilegeInventory.psm1'
)
$ContractPath = Join-Path $Root 'docs/contract.json'
$SourcesPath = Join-Path $Root 'docs/official_sources.json'
$MockPath = Join-Path $PSScriptRoot 'mock_vcenter.py'
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'vcf91-0098-' + [guid]::NewGuid().ToString('N')
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

function New-HttpStatusException {
    param(
        [Parameter(Mandatory)]
        [int] $Status
    )
    return [Net.Http.HttpRequestException]::new(
        "vCenter fixture returned HTTP $Status.",
        $null,
        [Net.HttpStatusCode] $Status
    )
}

try {
    $Contract = Get-Content -Raw -LiteralPath $ContractPath | ConvertFrom-Json
    $Sources = Get-Content -Raw -LiteralPath $SourcesPath | ConvertFrom-Json
    Assert-Equal $Sources.repository 'vmware/vcf-api-specs' `
        'official repository'
    Assert-Equal $Sources.repositoryCommitSha `
        '3949fc33339fc5ea1b77eadb258f1cf49aa88e26' `
        'pinned repository commit'
    Assert-Equal $Sources.specPath `
        'specifications/vsphere/openapi/automation/vcenter.yaml' `
        'official specification path'
    Assert-Equal $Sources.specBlobSha `
        '8028b0824c4ff3503d05f44814f967938a795c40' `
        'official specification blob'
    Assert-Equal @($Sources.operationIds).Count 1 `
        'official operationId count'
    Assert-Equal $Sources.operationIds[0] `
        'Vcenter.Authorization.Privileges_list' `
        'official operationId'
    Assert-Equal $Sources.operations[0].repositoryCommitSha `
        $Sources.repositoryCommitSha `
        'operation records the repository commit'
    Assert-Equal $Sources.operations[0].specPath $Sources.specPath `
        'operation records the specification path'
    Assert-Equal $Contract.source.apiVersion '9.1.0.0' `
        'contract API version'
    Assert-Equal $Contract.source.specPath $Sources.specPath `
        'contract and provenance paths agree'
    Assert-Equal @($Contract.operations).Count 1 `
        'contract operation count'
    $Operation = $Contract.operations[0]
    Assert-Equal $Operation.operationId `
        'Vcenter.Authorization.Privileges_list' `
        'contract operationId'
    Assert-Equal $Operation.sdkCmdlet `
        'Invoke-VcenterAuthorizationPrivilegesList' `
        'contract SDK cmdlet'
    Assert-Equal $Operation.sdkIterationInitializer `
        'Initialize-VcenterAuthorizationPrivilegesIterationSpec' `
        'contract SDK iteration initializer'
    Assert-Equal $Operation.method 'GET' 'contract method'
    Assert-Equal $Operation.path `
        '/api/vcenter/authorization/privileges' `
        'contract route'
    Assert-Equal (($Operation.effectiveQueryFields.name) -join ',') `
        'is_on_parent,names,versions,page_size,marker' `
        'contract query projection'
    Assert-Equal $Contract.schemas.`
        'Vcenter.Authorization.Privileges.IterationSpec'.`
        properties.page_size.defaultWhenMissing 200 `
        'specification page-size default'
    Assert-Equal (($Contract.schemas.`
        'Vcenter.Authorization.Privileges.Info'.required) -join ',') `
        'description,name,on_parent,version' `
        'contract privilege-info requirements'

    $Manifest = Import-PowerShellDataFile -LiteralPath $ManifestPath
    Assert-Equal @($Manifest.RequiredModules).Count 1 `
        'manifest prerequisite count'
    Assert-Equal $Manifest.RequiredModules[0].ModuleName `
        'VMware.Sdk.vSphere' `
        'VCF PowerCLI SDK prerequisite'
    Assert-Equal ([version] $Manifest.RequiredModules[0].ModuleVersion) `
        ([version] '13.5.0.25380678') `
        'VCF PowerCLI SDK version'
    Assert-Equal (($Manifest.FunctionsToExport) -join ',') (
        'New-VcfVcenterPrivilegeInventorySession,' +
        'Get-VcfVcenterPrivilegeInventory'
    ) `
        'manifest exports'

    $Tokens = $null
    $ParseErrors = $null
    $Ast = [Management.Automation.Language.Parser]::ParseFile(
        $ModulePath,
        [ref] $Tokens,
        [ref] $ParseErrors
    )
    Assert-Equal @($ParseErrors).Count 0 'module parses without errors'
    $CommandNames = @(
        $Ast.FindAll(
            {
                param($Node)
                $Node -is [Management.Automation.Language.CommandAst]
            },
            $true
        ) | ForEach-Object { $_.GetCommandName() }
    )
    $SourceText = Get-Content -Raw -LiteralPath $ModulePath
    foreach ($Required in @(
        'VMware.Sdk.vSphere',
        'Initialize-VcenterAuthorizationPrivilegesIterationSpec',
        'Invoke-VcenterAuthorizationPrivilegesList'
    )) {
        Assert-True $SourceText.Contains($Required) `
            "production module must name $Required"
    }
    foreach ($Forbidden in @(
        'Invoke-RestMethod',
        'Invoke-WebRequest',
        'System.Net.Http.HttpClient',
        'curl',
        'Start-Process',
        'Connect-VIServer'
    )) {
        Assert-True (-not $SourceText.Contains($Forbidden)) `
            "production module must not contain $Forbidden"
    }
    Assert-True ($CommandNames -contains 'Import-Module') `
        'production path lazily imports the SDK'

    Import-Module $ModulePath -Force -ErrorAction Stop
    $Exports = @(
        Get-Command `
            -Module VcfVcenterPrivilegeInventory `
            -CommandType Function |
            Sort-Object Name |
            ForEach-Object Name
    )
    Assert-Equal ($Exports -join ',') (
        'Get-VcfVcenterPrivilegeInventory,' +
        'New-VcfVcenterPrivilegeInventorySession'
    ) `
        'runtime exports'

    $RunId = [guid]::NewGuid().ToString('N')
    $OldToken = 'old-' + $RunId.Substring(0, 12)
    $FreshToken = 'fresh-' + $RunId.Substring(12, 12)
    $MarkerOne = 'after ' + $RunId.Substring(24, 4) + '/one+?&'
    $MarkerTwo = 'after+' + $RunId.Substring(28, 4) + '/two ?'
    $Privileges = @(
        [ordered]@{
            privilege = 'zulu.' + $RunId.Substring(0, 4)
            info = [ordered]@{
                name = 'zulu.' + $RunId.Substring(0, 4)
                description = 'runtime zulu'
                on_parent = $false
                version = 4
            }
        },
        [ordered]@{
            privilege = 'Alpha.' + $RunId.Substring(4, 4)
            info = [ordered]@{
                name = 'Alpha.' + $RunId.Substring(4, 4)
                description = 'runtime uppercase alpha'
                on_parent = $true
                version = 2
            }
        },
        [ordered]@{
            privilege = 'alpha.' + $RunId.Substring(8, 4)
            info = [ordered]@{
                name = 'alpha.' + $RunId.Substring(8, 4)
                description = 'runtime lowercase alpha'
                on_parent = $false
                version = 8
            }
        },
        [ordered]@{
            privilege = 'Bravo.' + $RunId.Substring(12, 4)
            info = [ordered]@{
                name = 'Bravo.' + $RunId.Substring(12, 4)
                description = 'runtime uppercase bravo'
                on_parent = $true
                version = 3
            }
        },
        [ordered]@{
            privilege = 'bravo.' + $RunId.Substring(16, 4)
            info = [ordered]@{
                name = 'bravo.' + $RunId.Substring(16, 4)
                description = 'runtime lowercase bravo'
                on_parent = $false
                version = 5
            }
        },
        [ordered]@{
            privilege = 'Charlie.' + $RunId.Substring(20, 4)
            info = [ordered]@{
                name = 'Charlie.' + $RunId.Substring(20, 4)
                description = 'runtime uppercase charlie'
                on_parent = $true
                version = 6
            }
        }
    )
    $Scenario = [ordered]@{
        old_token = $OldToken
        fresh_token = $FreshToken
        page_size = 2
        expiry_marker = $MarkerOne
        pages = @(
            [ordered]@{
                incoming_marker = $null
                outgoing_marker = $MarkerOne
                items = @($Privileges[0], $Privileges[3])
            },
            [ordered]@{
                incoming_marker = $MarkerOne
                outgoing_marker = $MarkerTwo
                items = @($Privileges[4], $Privileges[1])
            },
            [ordered]@{
                incoming_marker = $MarkerTwo
                outgoing_marker = $null
                items = @($Privileges[2], $Privileges[5])
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
    $InvocationLog = [Collections.Generic.List[object]]::new()
    $AllowedParameterNames = @('PageSize', 'Marker')
    $OperationId = [string] $Operation.operationId
    $OperationPath = [string] $Operation.path

    $OperationInvoker = {
        param(
            [string] $RequestedOperationId,
            [hashtable] $Parameters,
            $Server
        )

        if ($RequestedOperationId -cne $OperationId) {
            throw "operation outside contract: $RequestedOperationId"
        }
        if ($null -eq $Parameters -or -not $Parameters.ContainsKey('PageSize')) {
            throw 'page_size must be explicitly supplied'
        }
        foreach ($Key in @($Parameters.Keys)) {
            if ($AllowedParameterNames -cnotcontains [string] $Key) {
                throw "unexpected operation parameter: $Key"
            }
        }
        foreach ($Unset in @(
            'Filter',
            'IsOnParent',
            'Names',
            'Versions',
            'Iterate'
        )) {
            if ($Parameters.ContainsKey($Unset)) {
                throw "unset optional parameter was supplied: $Unset"
            }
        }

        $QueryParts = [Collections.Generic.List[string]]::new()
        $QueryParts.Add(
            'page_size=' +
            [uri]::EscapeDataString([string] $Parameters.PageSize)
        )
        $Marker = $null
        if ($Parameters.ContainsKey('Marker')) {
            $Marker = [string] $Parameters.Marker
            if ([string]::IsNullOrEmpty($Marker)) {
                throw 'an empty marker must be omitted'
            }
            $QueryParts.Add(
                'marker=' + [uri]::EscapeDataString($Marker)
            )
        }
        $Token = [string] $Server.AccessToken
        $InvocationLog.Add([pscustomobject]@{
            OperationId = $RequestedOperationId
            PageSize = [long] $Parameters.PageSize
            Marker = $Marker
            Token = $Token
            KeySet = (@($Parameters.Keys | Sort-Object) -join ',')
        })
        $Uri = $BaseUrl + $OperationPath + '?' + ($QueryParts -join '&')
        $Response = Invoke-WebRequest `
            -Uri $Uri `
            -Method Get `
            -Headers @{
                'vmware-api-session-id' = $Token
                Accept = 'application/json'
            } `
            -SkipHttpErrorCheck
        $Status = [int] $Response.StatusCode
        if ($Status -ne 200) {
            throw (New-HttpStatusException -Status $Status)
        }
        return $Response.Content | ConvertFrom-Json
    }.GetNewClosure()

    $RefreshState = [pscustomobject]@{ Count = 0 }
    $RefreshConnection = {
        param($ExpiredServer)
        $RefreshState.Count++
        Assert-Equal $ExpiredServer.AccessToken $OldToken `
            'refresh receives the expired handle'
        return [pscustomobject]@{ AccessToken = $FreshToken }
    }.GetNewClosure()

    $Session = New-VcfVcenterPrivilegeInventorySession `
        -Server ([pscustomobject]@{ AccessToken = $OldToken }) `
        -RefreshConnection $RefreshConnection `
        -OperationInvoker $OperationInvoker
    Assert-Equal $InvocationLog.Count 0 `
        'session construction performs no traffic'

    $First = @(
        Get-VcfVcenterPrivilegeInventory -Session $Session -PageSize 2
    )
    $Second = @(
        Get-VcfVcenterPrivilegeInventory -Session $Session -PageSize 2
    )
    Assert-Equal $RefreshState.Count 1 'token refresh count'
    Assert-Equal $Session.Server.AccessToken $FreshToken `
        'session retains the replacement handle'
    Assert-Equal $First.Count 6 `
        'first run preserves all privileges'
    Assert-Equal $Second.Count 6 `
        'second run retrieves all privileges'
    $ExpectedPrivilegeOrder = @(
        $Privileges[1].privilege,
        $Privileges[3].privilege,
        $Privileges[5].privilege,
        $Privileges[2].privilege,
        $Privileges[4].privilege,
        $Privileges[0].privilege
    )
    Assert-Equal (($First.privilege) -join ',') `
        ($ExpectedPrivilegeOrder -join ',') `
        'first inventory uses complete ordinal privilege ordering'
    Assert-Equal (($Second.privilege) -join ',') `
        ($ExpectedPrivilegeOrder -join ',') `
        'second inventory remains stable after wire-order flips'
    Assert-Equal (($First.info.description) -join ',') `
        (($Privileges[1].info.description,
          $Privileges[3].info.description,
          $Privileges[5].info.description,
          $Privileges[2].info.description,
          $Privileges[4].info.description,
          $Privileges[0].info.description) -join ',') `
        'complete privilege info is preserved'

    Start-Sleep -Milliseconds 75
    $Requests = @(
        Get-Content -LiteralPath $LogPath |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
    Assert-Equal $Requests.Count 7 `
        'exact request count across both runs'
    Assert-Equal $InvocationLog.Count 7 `
        'exact SDK-operation invocation count'
    $ExpectedMarkers = @(
        $null,
        $MarkerOne,
        $MarkerOne,
        $MarkerTwo,
        $null,
        $MarkerOne,
        $MarkerTwo
    )
    $ExpectedTokens = @(
        $OldToken,
        $OldToken,
        $FreshToken,
        $FreshToken,
        $FreshToken,
        $FreshToken,
        $FreshToken
    )
    $ExpectedStatuses = @(200, 401, 200, 200, 200, 200, 200)
    $ExpectedFlip = @($true, $null, $false, $true, $false, $true, $false)
    $ForbiddenQueryFields = @(
        'filter',
        'is_on_parent',
        'names',
        'versions',
        'iterate'
    )
    for ($Index = 0; $Index -lt $Requests.Count; $Index++) {
        $Request = $Requests[$Index]
        $ExpectedQuery = 'page_size=2'
        if ($null -ne $ExpectedMarkers[$Index]) {
            $ExpectedQuery += '&marker=' +
                [uri]::EscapeDataString([string] $ExpectedMarkers[$Index])
        }
        Assert-Equal $Request.operationId $OperationId `
            "request $Index operationId"
        Assert-Equal $Request.method 'GET' "request $Index method"
        Assert-Equal $Request.path $OperationPath "request $Index path"
        Assert-Equal $Request.rawQuery $ExpectedQuery `
            "request $Index exact raw query and field order"
        Assert-Equal $Request.rawTarget ($OperationPath + '?' + $ExpectedQuery) `
            "request $Index exact raw target"
        Assert-Equal $Request.vmwareApiSessionId $ExpectedTokens[$Index] `
            "request $Index api session token"
        Assert-True ($null -eq $Request.authorization) `
            "request $Index omits Authorization"
        Assert-True ($Request.accept -like 'application/json*') `
            "request $Index Accept header"
        Assert-True ($null -eq $Request.contentType) `
            "request $Index omits Content-Type"
        Assert-Equal ([int] $Request.contentLength) 0 `
            "request $Index has no body"
        Assert-Equal $Request.bodyHex '' `
            "request $Index exact empty body"
        Assert-Equal ([int] $Request.status) $ExpectedStatuses[$Index] `
            "request $Index status"
        Assert-Equal $Request.collectionReversed $ExpectedFlip[$Index] `
            "request $Index collection-order flip"
        foreach ($Field in $ForbiddenQueryFields) {
            Assert-True (
                $Request.query.PSObject.Properties.Name -cnotcontains $Field
            ) "request $Index omits optional query field $Field"
            Assert-True (
                $Request.rawQuery -cnotmatch (
                    '(^|&)' + [regex]::Escape($Field) + '='
                )
            ) "request $Index has no serialized $Field key"
        }

        Assert-Equal $InvocationLog[$Index].OperationId $OperationId `
            "invocation $Index operationId"
        Assert-Equal $InvocationLog[$Index].PageSize ([long] 2) `
            "invocation $Index page size"
        Assert-Equal $InvocationLog[$Index].Marker $ExpectedMarkers[$Index] `
            "invocation $Index marker"
        $ExpectedKeys = if ($null -eq $ExpectedMarkers[$Index]) {
            'PageSize'
        }
        else {
            'Marker,PageSize'
        }
        Assert-Equal $InvocationLog[$Index].KeySet $ExpectedKeys `
            "invocation $Index omits unset SDK parameters"
    }
    Assert-Equal $Requests[1].rawTarget $Requests[2].rawTarget `
        '401 retry uses the identical target'
    Assert-Equal $Requests[1].method $Requests[2].method `
        '401 retry uses the identical method'
    Assert-Equal $Requests[0].rawTarget `
        $Requests[4].rawTarget `
        'a later call begins at the first page with the retained handle'

    $AlwaysUnauthorizedCalls = [pscustomobject]@{ Count = 0 }
    $AlwaysUnauthorized = {
        param($RequestedOperationId, $Parameters, $Server)
        $AlwaysUnauthorizedCalls.Count++
        throw (New-HttpStatusException -Status 401)
    }.GetNewClosure()
    $RetryRefresh = [pscustomobject]@{ Count = 0 }
    $RetrySession = New-VcfVcenterPrivilegeInventorySession `
        -Server ([pscustomobject]@{
            AccessToken = 'runtime-expired-a'
        }) `
        -RefreshConnection {
            param($ExpiredServer)
            $RetryRefresh.Count++
            [pscustomobject]@{ AccessToken = 'runtime-expired-b' }
        }.GetNewClosure() `
        -OperationInvoker $AlwaysUnauthorized
    $RetryError = $null
    try {
        Get-VcfVcenterPrivilegeInventory `
            -Session $RetrySession `
            -PageSize 2 > $null
    }
    catch {
        $RetryError = $_
    }
    Assert-True ($null -ne $RetryError) `
        'a second 401 is terminal'
    Assert-Equal $AlwaysUnauthorizedCalls.Count 2 `
        'a failed request is attempted only twice'
    Assert-Equal $RetryRefresh.Count 1 `
        'a failed request refreshes only once'
    Assert-True (
        $RetryError.Exception.Message -cnotmatch 'runtime-expired'
    ) 'authentication error does not leak token text'

    $RefreshFailureSession = New-VcfVcenterPrivilegeInventorySession `
        -Server ([pscustomobject]@{
            AccessToken = 'runtime-refresh-secret'
        }) `
        -RefreshConnection {
            param($ExpiredServer)
            throw 'nested-refresh-secret'
        } `
        -OperationInvoker {
            param($RequestedOperationId, $Parameters, $Server)
            throw (New-HttpStatusException -Status 401)
        }
    $RefreshFailure = $null
    try {
        Get-VcfVcenterPrivilegeInventory `
            -Session $RefreshFailureSession `
            -PageSize 2 > $null
    }
    catch {
        $RefreshFailure = $_
    }
    Assert-True ($null -ne $RefreshFailure) `
        'refresh callback failure is terminal'
    Assert-True (
        $RefreshFailure.Exception.Message -cnotmatch (
            'runtime-refresh-secret|nested-refresh-secret'
        )
    ) 'refresh failure is sanitized'

    $ServerFailureCalls = [pscustomobject]@{ Count = 0 }
    $ServerFailureRefresh = [pscustomobject]@{ Count = 0 }
    $ServerFailureSession = New-VcfVcenterPrivilegeInventorySession `
        -Server ([pscustomobject]@{
            AccessToken = 'runtime-server-failure'
        }) `
        -RefreshConnection {
            param($ExpiredServer)
            $ServerFailureRefresh.Count++
            [pscustomobject]@{ AccessToken = 'must-not-be-used' }
        }.GetNewClosure() `
        -OperationInvoker {
            param($RequestedOperationId, $Parameters, $Server)
            $ServerFailureCalls.Count++
            throw (New-HttpStatusException -Status 500)
        }.GetNewClosure()
    $ServerError = $null
    try {
        Get-VcfVcenterPrivilegeInventory `
            -Session $ServerFailureSession `
            -PageSize 2 > $null
    }
    catch {
        $ServerError = $_
    }
    Assert-True ($null -ne $ServerError) `
        'non-401 failure propagates'
    Assert-Equal $ServerFailureCalls.Count 1 `
        'non-401 is not retried'
    Assert-Equal $ServerFailureRefresh.Count 0 `
        'non-401 never refreshes'

    $DefaultPageSizeCalls = [pscustomobject]@{ Count = 0 }
    $DefaultPageSizeSession = New-VcfVcenterPrivilegeInventorySession `
        -Server ([pscustomobject]@{
            AccessToken = 'runtime-default-size'
        }) `
        -RefreshConnection {
            param($ExpiredServer)
            throw 'refresh must not be called'
        } `
        -OperationInvoker {
            param($RequestedOperationId, $Parameters, $Server)
            $DefaultPageSizeCalls.Count++
            Assert-Equal ([long] $Parameters.PageSize) ([long] 200) `
                'omitted PageSize uses specification default'
            Assert-Equal (@($Parameters.Keys | Sort-Object) -join ',') `
                'PageSize' `
                'default request omits marker and filters'
            return [pscustomobject]@{ items = @() }
        }.GetNewClosure()
    $DefaultResult = @(
        Get-VcfVcenterPrivilegeInventory `
            -Session $DefaultPageSizeSession
    )
    Assert-Equal $DefaultPageSizeCalls.Count 1 `
        'default page-size inventory invokes one terminal page'
    Assert-Equal $DefaultResult.Count 0 `
        'an empty terminal inventory succeeds'

    $InvalidPageSizeCalls = [pscustomobject]@{ Count = 0 }
    $InvalidPageSizeSession = New-VcfVcenterPrivilegeInventorySession `
        -Server ([pscustomobject]@{
            AccessToken = 'runtime-invalid-size'
        }) `
        -RefreshConnection {
            param($ExpiredServer)
            throw 'refresh must not be called'
        } `
        -OperationInvoker {
            param($RequestedOperationId, $Parameters, $Server)
            $InvalidPageSizeCalls.Count++
            return [pscustomobject]@{ items = @() }
        }.GetNewClosure()
    $InvalidPageSizeError = $null
    try {
        Get-VcfVcenterPrivilegeInventory `
            -Session $InvalidPageSizeSession `
            -PageSize 0 > $null
    }
    catch {
        $InvalidPageSizeError = $_
    }
    Assert-True ($null -ne $InvalidPageSizeError) `
        'a non-positive PageSize is rejected'
    Assert-Equal $InvalidPageSizeCalls.Count 0 `
        'invalid PageSize is rejected before an operation call'

    $ValidItem = [pscustomobject]@{
        privilege = 'runtime-valid-privilege'
        info = [pscustomobject]@{
            name = 'runtime-valid-privilege'
            description = 'runtime valid'
            on_parent = $false
            version = 1
        }
    }
    $ValidationCases = @(
        [pscustomobject]@{
            Name = 'null page'
            Page = $null
        },
        [pscustomobject]@{
            Name = 'missing Items'
            Page = [pscustomobject]@{}
        },
        [pscustomobject]@{
            Name = 'null Items'
            Page = [pscustomobject]@{ items = $null }
        },
        [pscustomobject]@{
            Name = 'null item'
            Page = [pscustomobject]@{
                items = [object[]] @($null)
            }
        },
        [pscustomobject]@{
            Name = 'blank Privilege'
            Page = [pscustomobject]@{
                items = @(
                    [pscustomobject]@{
                        privilege = ' '
                        info = $ValidItem.info
                    }
                )
            }
        },
        [pscustomobject]@{
            Name = 'missing Info'
            Page = [pscustomobject]@{
                items = @(
                    [pscustomobject]@{
                        privilege = 'runtime-privilege'
                    }
                )
            }
        },
        [pscustomobject]@{
            Name = 'blank Info.Name'
            Page = [pscustomobject]@{
                items = @(
                    [pscustomobject]@{
                        privilege = 'runtime-privilege'
                        info = [pscustomobject]@{ name = '' }
                    }
                )
            }
        },
        [pscustomobject]@{
            Name = 'non-string marker'
            Page = [pscustomobject]@{
                items = @($ValidItem)
                marker = 42
            }
        },
        [pscustomobject]@{
            Name = 'empty marker'
            Page = [pscustomobject]@{
                items = @($ValidItem)
                marker = ''
            }
        },
        [pscustomobject]@{
            Name = 'empty non-terminal page'
            Page = [pscustomobject]@{
                items = @()
                marker = 'runtime-next'
            }
        }
    )
    foreach ($Case in $ValidationCases) {
        $CaseRefresh = [pscustomobject]@{ Count = 0 }
        $CasePage = $Case.Page
        $CaseSession = New-VcfVcenterPrivilegeInventorySession `
            -Server ([pscustomobject]@{
                AccessToken = 'runtime-validation'
            }) `
            -RefreshConnection {
                param($ExpiredServer)
                $CaseRefresh.Count++
                [pscustomobject]@{
                    AccessToken = 'unexpected-refresh'
                }
            }.GetNewClosure() `
            -OperationInvoker {
                param($RequestedOperationId, $Parameters, $Server)
                return $CasePage
            }.GetNewClosure()
        $CaseError = $null
        try {
            Get-VcfVcenterPrivilegeInventory `
                -Session $CaseSession `
                -PageSize 2 > $null
        }
        catch {
            $CaseError = $_
        }
        Assert-True ($null -ne $CaseError) `
            "$($Case.Name) is rejected"
        Assert-Equal $CaseRefresh.Count 0 `
            "$($Case.Name) does not refresh"
    }

    $RepeatedMarkerCalls = [pscustomobject]@{ Count = 0 }
    $RepeatedMarker = 'runtime-repeated-marker'
    $RepeatedMarkerSession = New-VcfVcenterPrivilegeInventorySession `
        -Server ([pscustomobject]@{
            AccessToken = 'runtime-repeat'
        }) `
        -RefreshConnection {
            param($ExpiredServer)
            throw 'refresh must not be called'
        } `
        -OperationInvoker {
            param($RequestedOperationId, $Parameters, $Server)
            $RepeatedMarkerCalls.Count++
            [pscustomobject]@{
                items = @(
                    [pscustomobject]@{
                        privilege = (
                            "runtime-privilege-$($RepeatedMarkerCalls.Count)"
                        )
                        info = [pscustomobject]@{
                            name = (
                                "runtime-name-$($RepeatedMarkerCalls.Count)"
                            )
                        }
                    }
                )
                marker = $RepeatedMarker
            }
        }.GetNewClosure()
    $RepeatedMarkerError = $null
    try {
        Get-VcfVcenterPrivilegeInventory `
            -Session $RepeatedMarkerSession `
            -PageSize 2 > $null
    }
    catch {
        $RepeatedMarkerError = $_
    }
    Assert-True ($null -ne $RepeatedMarkerError) `
        'a repeated marker is rejected'
    Assert-Equal $RepeatedMarkerCalls.Count 2 `
        'repeated-marker detection terminates promptly'

    $BufferedCalls = [pscustomobject]@{ Count = 0 }
    $ObservedPartialOutput = [Collections.Generic.List[object]]::new()
    $BufferedSession = New-VcfVcenterPrivilegeInventorySession `
        -Server ([pscustomobject]@{
            AccessToken = 'runtime-buffered'
        }) `
        -RefreshConnection {
            param($ExpiredServer)
            throw 'refresh must not be called'
        } `
        -OperationInvoker {
            param($RequestedOperationId, $Parameters, $Server)
            $BufferedCalls.Count++
            if ($BufferedCalls.Count -eq 1) {
                return [pscustomobject]@{
                    items = @(
                        [pscustomobject]@{
                            privilege = 'must-not-escape'
                            info = [pscustomobject]@{
                                name = 'must-not-escape'
                            }
                        }
                    )
                    marker = 'runtime-later-page'
                }
            }
            throw (New-HttpStatusException -Status 500)
        }.GetNewClosure()
    $BufferedError = $null
    try {
        Get-VcfVcenterPrivilegeInventory `
            -Session $BufferedSession `
            -PageSize 2 |
            ForEach-Object { $ObservedPartialOutput.Add($_) }
    }
    catch {
        $BufferedError = $_
    }
    Assert-True ($null -ne $BufferedError) `
        'later-page failure propagates'
    Assert-Equal $BufferedCalls.Count 2 `
        'later-page failure stops collection'
    Assert-Equal $ObservedPartialOutput.Count 0 `
        'no partial privilege objects escape before all pages succeed'

    Write-Output 'ALL TESTS PASSED'
}
finally {
    if ($null -ne $MockProcess -and -not $MockProcess.HasExited) {
        Stop-Process -Id $MockProcess.Id -Force -ErrorAction SilentlyContinue
        $MockProcess.WaitForExit()
    }
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force `
            -ErrorAction SilentlyContinue
    }
    Remove-Module VcfVcenterPrivilegeInventory `
        -Force `
        -ErrorAction SilentlyContinue
}
