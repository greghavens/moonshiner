$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $Root 'VcfVcenterRoleInventory/VcfVcenterRoleInventory.psd1'
$ModulePath = Join-Path $Root 'VcfVcenterRoleInventory/VcfVcenterRoleInventory.psm1'
$ContractPath = Join-Path $Root 'docs/contract.json'
$SourcesPath = Join-Path $Root 'docs/official_sources.json'
$MockPath = Join-Path $PSScriptRoot 'mock_vcenter.py'
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'vcf91-0090-' + [guid]::NewGuid().ToString('N')
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
    Assert-Equal $Sources.repository 'vmware/vcf-api-specs' 'official repository'
    Assert-Equal $Sources.repositoryCommitSha `
        '3949fc33339fc5ea1b77eadb258f1cf49aa88e26' 'pinned repository commit'
    Assert-Equal $Sources.specPath `
        'specifications/vsphere/openapi/automation/vcenter.yaml' 'official spec path'
    Assert-Equal $Sources.specBlobSha `
        '8028b0824c4ff3503d05f44814f967938a795c40' 'official spec blob'
    Assert-Equal @($Sources.operationIds).Count 1 'official operationId count'
    Assert-Equal $Sources.operationIds[0] `
        'Vcenter.Authorization.Roles_list' 'official operationId'
    Assert-Equal $Contract.source.apiVersion '9.1.0.0' 'contract API version'
    Assert-Equal $Contract.source.specPath $Sources.specPath `
        'contract and provenance spec paths agree'
    Assert-Equal @($Contract.operations).Count 1 'contract operation count'
    $Operation = $Contract.operations[0]
    Assert-Equal $Operation.operationId `
        'Vcenter.Authorization.Roles_list' 'contract operationId'
    Assert-Equal $Operation.sdkCmdlet `
        'Invoke-VcenterAuthorizationRolesList' 'contract SDK cmdlet'
    Assert-Equal $Operation.sdkIterationInitializer `
        'Initialize-VcenterAuthorizationRolesIterationSpec' `
        'contract SDK iteration initializer'
    Assert-Equal $Operation.method 'GET' 'contract method'
    Assert-Equal $Operation.path `
        '/api/vcenter/authorization/roles' 'contract route'
    Assert-Equal (($Operation.effectiveQueryFields.name) -join ',') `
        'is_system,names,privileges,page_size,marker' `
        'contract query projection'
    Assert-Equal $Contract.schemas.'Vcenter.Authorization.Roles.IterationSpec'.`
        properties.page_size.defaultWhenMissing 200 'specification page-size default'

    $Manifest = Import-PowerShellDataFile -LiteralPath $ManifestPath
    Assert-Equal @($Manifest.RequiredModules).Count 1 'manifest prerequisite count'
    Assert-Equal $Manifest.RequiredModules[0].ModuleName `
        'VMware.Sdk.vSphere' 'VCF PowerCLI SDK prerequisite'
    Assert-Equal ([version] $Manifest.RequiredModules[0].ModuleVersion) `
        ([version] '13.5.0.25380678') 'VCF PowerCLI SDK version'
    Assert-Equal (($Manifest.FunctionsToExport) -join ',') `
        'New-VcfVcenterRoleInventorySession,Get-VcfVcenterRoleInventory' `
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
        'Initialize-VcenterAuthorizationRolesIterationSpec',
        'Invoke-VcenterAuthorizationRolesList'
    )) {
        Assert-True $SourceText.Contains($Required) `
            "production module must name $Required"
    }
    foreach ($Forbidden in @(
        'Invoke-RestMethod',
        'Invoke-WebRequest',
        'System.Net.Http.HttpClient',
        'curl',
        'Start-Process'
    )) {
        Assert-True (-not $SourceText.Contains($Forbidden)) `
            "production module must not contain $Forbidden"
    }
    Assert-True ($CommandNames -contains 'Import-Module') `
        'production path lazily imports the SDK'

    Import-Module $ModulePath -Force -ErrorAction Stop
    $Exports = @(
        Get-Command -Module VcfVcenterRoleInventory -CommandType Function |
            Sort-Object Name |
            ForEach-Object Name
    )
    Assert-Equal ($Exports -join ',') `
        'Get-VcfVcenterRoleInventory,New-VcfVcenterRoleInventorySession' `
        'runtime exports'

    $RunId = [guid]::NewGuid().ToString('N')
    $OldToken = 'old-' + $RunId.Substring(0, 12)
    $FreshToken = 'fresh-' + $RunId.Substring(12, 12)
    $MarkerOne = 'after ' + $RunId.Substring(24, 4) + '/one+?&'
    $MarkerTwo = 'after+' + $RunId.Substring(28, 4) + '/two ?'
    $Roles = @(
        [ordered]@{
            role = 'role-z-' + $RunId.Substring(0, 4)
            info = [ordered]@{
                name = 'zulu'
                description = 'runtime zulu'
                privileges = @('System.Read')
                system = $false
            }
        },
        [ordered]@{
            role = 'role-b-' + $RunId.Substring(4, 4)
            info = [ordered]@{
                name = 'Alpha'
                description = 'runtime alpha b'
                privileges = @('System.Read', 'System.View')
                system = $false
            }
        },
        [ordered]@{
            role = 'role-a-' + $RunId.Substring(8, 4)
            info = [ordered]@{
                name = 'Alpha'
                description = 'runtime alpha a'
                privileges = @('System.Read')
                system = $false
            }
        },
        [ordered]@{
            role = 'role-c-' + $RunId.Substring(12, 4)
            info = [ordered]@{
                name = 'bravo'
                description = 'runtime bravo'
                privileges = @('System.Read')
                system = $true
            }
        },
        [ordered]@{
            role = 'role-d-' + $RunId.Substring(16, 4)
            info = [ordered]@{
                name = 'alpha'
                description = 'runtime lowercase alpha'
                privileges = @('System.Read')
                system = $false
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
        foreach ($Unset in @('Filter', 'IsSystem', 'Names', 'Privileges')) {
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

    $Session = New-VcfVcenterRoleInventorySession `
        -Server ([pscustomobject]@{ AccessToken = $OldToken }) `
        -RefreshConnection $RefreshConnection `
        -OperationInvoker $OperationInvoker
    Assert-Equal $InvocationLog.Count 0 'session construction performs no traffic'

    $First = @(Get-VcfVcenterRoleInventory -Session $Session -PageSize 2)
    $Second = @(Get-VcfVcenterRoleInventory -Session $Session -PageSize 2)
    Assert-Equal $RefreshState.Count 1 'token refresh count'
    Assert-Equal $Session.Server.AccessToken $FreshToken `
        'session retains the replacement handle'
    Assert-Equal $First.Count 5 'first run preserves all roles'
    Assert-Equal $Second.Count 5 'second run retrieves all roles'
    $ExpectedRoleOrder = @(
        $Roles[2].role,
        $Roles[1].role,
        $Roles[4].role,
        $Roles[3].role,
        $Roles[0].role
    )
    Assert-Equal (($First.role) -join ',') ($ExpectedRoleOrder -join ',') `
        'first inventory uses complete ordinal name/role ordering'
    Assert-Equal (($Second.role) -join ',') ($ExpectedRoleOrder -join ',') `
        'second inventory is stable'

    Start-Sleep -Milliseconds 75
    $Requests = @(
        Get-Content -LiteralPath $LogPath |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
    Assert-Equal $Requests.Count 7 'exact request count across both runs'
    Assert-Equal $InvocationLog.Count 7 'exact SDK-operation invocation count'
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
    $ForbiddenQueryFields = @(
        'filter',
        'is_system',
        'names',
        'privileges',
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
        Assert-Equal $Request.vmwareApiSessionId `
            $ExpectedTokens[$Index] `
            "request $Index api session token"
        Assert-True ($null -eq $Request.authorization) `
            "request $Index omits out-of-contract Authorization header"
        Assert-True ($Request.accept -like 'application/json*') `
            "request $Index Accept header"
        Assert-True ($null -eq $Request.contentType) `
            "request $Index omits Content-Type"
        Assert-Equal ([int] $Request.contentLength) 0 `
            "request $Index has no body"
        Assert-Equal $Request.bodyHex '' "request $Index exact empty body"
        Assert-Equal ([int] $Request.status) $ExpectedStatuses[$Index] `
            "request $Index status"
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

    $AlwaysUnauthorizedCalls = [pscustomobject]@{ Count = 0 }
    $AlwaysUnauthorized = {
        param($RequestedOperationId, $Parameters, $Server)
        $AlwaysUnauthorizedCalls.Count++
        throw (New-HttpStatusException -Status 401)
    }.GetNewClosure()
    $RetryRefresh = [pscustomobject]@{ Count = 0 }
    $RetrySession = New-VcfVcenterRoleInventorySession `
        -Server ([pscustomobject]@{ AccessToken = 'runtime-expired-a' }) `
        -RefreshConnection {
            param($ExpiredServer)
            $RetryRefresh.Count++
            [pscustomobject]@{ AccessToken = 'runtime-expired-b' }
        }.GetNewClosure() `
        -OperationInvoker $AlwaysUnauthorized
    $RetryError = $null
    try {
        Get-VcfVcenterRoleInventory -Session $RetrySession -PageSize 2 > $null
    }
    catch {
        $RetryError = $_
    }
    Assert-True ($null -ne $RetryError) 'a second 401 is terminal'
    Assert-Equal $AlwaysUnauthorizedCalls.Count 2 `
        'a failed request is attempted only twice'
    Assert-Equal $RetryRefresh.Count 1 'a failed request refreshes only once'
    Assert-True (
        $RetryError.Exception.Message -cnotmatch 'runtime-expired'
    ) 'authentication error does not leak token text'

    $ServerFailureCalls = [pscustomobject]@{ Count = 0 }
    $ServerFailureRefresh = [pscustomobject]@{ Count = 0 }
    $ServerFailureSession = New-VcfVcenterRoleInventorySession `
        -Server ([pscustomobject]@{ AccessToken = 'runtime-server-failure' }) `
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
        Get-VcfVcenterRoleInventory `
            -Session $ServerFailureSession `
            -PageSize 2 > $null
    }
    catch {
        $ServerError = $_
    }
    Assert-True ($null -ne $ServerError) 'non-401 failure propagates'
    Assert-Equal $ServerFailureCalls.Count 1 'non-401 is not retried'
    Assert-Equal $ServerFailureRefresh.Count 0 'non-401 never refreshes'

    $DefaultPageSizeCalls = [pscustomobject]@{ Count = 0 }
    $DefaultPageSizeSession = New-VcfVcenterRoleInventorySession `
        -Server ([pscustomobject]@{ AccessToken = 'runtime-default-size' }) `
        -RefreshConnection {
            param($ExpiredServer)
            throw 'refresh must not be called'
        } `
        -OperationInvoker {
            param($RequestedOperationId, $Parameters, $Server)
            $DefaultPageSizeCalls.Count++
            Assert-Equal ([long] $Parameters.PageSize) ([long] 200) `
                'omitted PageSize uses the specification default'
            Assert-Equal (@($Parameters.Keys | Sort-Object) -join ',') `
                'PageSize' 'default request omits marker and filter fields'
            return [pscustomobject]@{ items = @() }
        }.GetNewClosure()
    $DefaultResult = @(
        Get-VcfVcenterRoleInventory -Session $DefaultPageSizeSession
    )
    Assert-Equal $DefaultPageSizeCalls.Count 1 `
        'default page-size inventory invokes one terminal page'
    Assert-Equal $DefaultResult.Count 0 `
        'an empty terminal inventory succeeds'

    $InvalidPageSizeCalls = [pscustomobject]@{ Count = 0 }
    $InvalidPageSizeSession = New-VcfVcenterRoleInventorySession `
        -Server ([pscustomobject]@{ AccessToken = 'runtime-invalid-size' }) `
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
        Get-VcfVcenterRoleInventory `
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
            Name = 'blank Role'
            Page = [pscustomobject]@{
                items = @(
                    [pscustomobject]@{
                        role = ' '
                        info = [pscustomobject]@{ name = 'runtime name' }
                    }
                )
            }
        },
        [pscustomobject]@{
            Name = 'missing Info'
            Page = [pscustomobject]@{
                items = @([pscustomobject]@{ role = 'runtime-role' })
            }
        },
        [pscustomobject]@{
            Name = 'blank Info.Name'
            Page = [pscustomobject]@{
                items = @(
                    [pscustomobject]@{
                        role = 'runtime-role'
                        info = [pscustomobject]@{ name = '' }
                    }
                )
            }
        },
        [pscustomobject]@{
            Name = 'non-string marker'
            Page = [pscustomobject]@{
                items = @(
                    [pscustomobject]@{
                        role = 'runtime-role'
                        info = [pscustomobject]@{ name = 'runtime name' }
                    }
                )
                marker = 42
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
        $CaseSession = New-VcfVcenterRoleInventorySession `
            -Server ([pscustomobject]@{ AccessToken = 'runtime-validation' }) `
            -RefreshConnection {
                param($ExpiredServer)
                $CaseRefresh.Count++
                [pscustomobject]@{ AccessToken = 'unexpected-refresh' }
            }.GetNewClosure() `
            -OperationInvoker {
                param($RequestedOperationId, $Parameters, $Server)
                return $CasePage
            }.GetNewClosure()
        $CaseError = $null
        try {
            Get-VcfVcenterRoleInventory `
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
    $RepeatedMarkerSession = New-VcfVcenterRoleInventorySession `
        -Server ([pscustomobject]@{ AccessToken = 'runtime-repeat' }) `
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
                        role = "runtime-role-$($RepeatedMarkerCalls.Count)"
                        info = [pscustomobject]@{
                            name = "runtime-name-$($RepeatedMarkerCalls.Count)"
                        }
                    }
                )
                marker = $RepeatedMarker
            }
        }.GetNewClosure()
    $RepeatedMarkerError = $null
    try {
        Get-VcfVcenterRoleInventory `
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
    $BufferedSession = New-VcfVcenterRoleInventorySession `
        -Server ([pscustomobject]@{ AccessToken = 'runtime-buffered' }) `
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
                            role = 'must-not-escape'
                            info = [pscustomobject]@{ name = 'buffered' }
                        }
                    )
                    marker = 'runtime-later-page'
                }
            }
            throw (New-HttpStatusException -Status 500)
        }.GetNewClosure()
    $BufferedError = $null
    try {
        Get-VcfVcenterRoleInventory `
            -Session $BufferedSession `
            -PageSize 2 |
            ForEach-Object { $ObservedPartialOutput.Add($_) }
    }
    catch {
        $BufferedError = $_
    }
    Assert-True ($null -ne $BufferedError) 'later-page failure propagates'
    Assert-Equal $BufferedCalls.Count 2 'later-page failure stops collection'
    Assert-Equal $ObservedPartialOutput.Count 0 `
        'no partial role objects escape before all pages succeed'

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
    Remove-Module VcfVcenterRoleInventory -Force -ErrorAction SilentlyContinue
}
