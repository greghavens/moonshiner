# Protected acceptance verifier for VcfDomainClusterMap.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$PSStyle.OutputRendering = 'PlainText'
[System.Globalization.CultureInfo]::CurrentCulture =
    [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture =
    [System.Globalization.CultureInfo]::InvariantCulture

$workspaceRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $workspaceRoot
$script:Checks = 0
$script:Failures = [System.Collections.Generic.List[string]]::new()

function Assert-True {
    param(
        [Parameter(Mandatory)] [string] $Label,
        [Parameter(Mandatory)] [bool] $Condition
    )
    $script:Checks++
    if ($Condition) { return }
    $script:Failures.Add($Label)
    Write-Output "FAIL $Label"
}

function Assert-Equal {
    param(
        [Parameter(Mandatory)] [string] $Label,
        $Expected,
        $Actual
    )
    $script:Checks++
    if ([string] $Expected -ceq [string] $Actual) { return }
    $failure = "$Label (expected <$Expected>, actual <$Actual>)"
    $script:Failures.Add($failure)
    Write-Output "FAIL $failure"
}

function Read-RequestLog {
    param([Parameter(Mandatory)] [string] $Path)
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

function Get-HeaderValues {
    param(
        [Parameter(Mandatory)] [object] $Request,
        [Parameter(Mandatory)] [string] $Name
    )
    $property = $Request.headerValues.PSObject.Properties[$Name.ToLowerInvariant()]
    if ($null -eq $property) { return @() }
    @($property.Value)
}

function Sort-Ordinal {
    param(
        [Parameter(Mandatory)] [object[]] $Values,
        [Parameter(Mandatory)] [string[]] $Keys
    )
    $copy = @($Values)
    [Array]::Sort(
        $copy,
        [System.Comparison[object]] {
            param($left, $right)
            foreach ($key in $Keys) {
                $comparison = [string]::CompareOrdinal(
                    [string] $left.$key,
                    [string] $right.$key
                )
                if ($comparison -ne 0) { return $comparison }
            }
            return 0
        }
    )
    $copy
}

$moduleManifest = Join-Path $PWD 'VcfDomainClusterMap/VcfDomainClusterMap.psd1'
$moduleSource = Join-Path $PWD 'VcfDomainClusterMap/VcfDomainClusterMap.psm1'
$contractPath = Join-Path $PWD 'docs/contract.json'
$sourcesPath = Join-Path $PWD 'docs/official_sources.json'
$mockPath = Join-Path $PSScriptRoot 'mock_sddc_manager.py'

foreach ($path in @(
    $moduleManifest,
    $moduleSource,
    $contractPath,
    $sourcesPath,
    $mockPath
)) {
    Assert-True "required file exists: $([IO.Path]::GetFileName($path))" (
        Test-Path -LiteralPath $path -PathType Leaf
    )
}

$sdk = Get-Module -ListAvailable -Name 'VMware.Sdk.Vcf.SddcManager' |
    Where-Object { $_.Version -eq [version] '13.5.0.25380678' } |
    Select-Object -First 1
if ($null -eq $sdk) {
    Write-Output (
        'FAIL prerequisite VMware.Sdk.Vcf.SddcManager ' +
        '13.5.0.25380678 is not installed'
    )
    exit 1
}

$vendored = @(
    Get-ChildItem -LiteralPath $PWD -Recurse -File |
        Where-Object {
            $_.Name -like 'VMware.Sdk.Vcf*' -or
            $_.FullName -match '[/\\]VMware\.Sdk\.Vcf[^/\\]*[/\\]' -or
            $_.Extension.ToLowerInvariant() -in @(
                '.dll', '.nupkg', '.snupkg', '.zip'
            )
        }
)
Assert-Equal 'seed vendors no VMware module or binary dependency' 0 $vendored.Count

$manifest = Import-PowerShellDataFile -LiteralPath $moduleManifest
$requiredModuleNames = @(
    $manifest.RequiredModules | ForEach-Object {
        if ($_ -is [string]) { $_ } else { $_.ModuleName }
    }
)
Assert-True 'manifest keeps the genuine SDK prerequisite' (
    $requiredModuleNames -ccontains 'VMware.Sdk.Vcf.SddcManager'
)
Assert-Equal 'manifest exports only the requested function' `
    'Get-VcfDomainClusterMap' (($manifest.FunctionsToExport) -join ',')

$sourceText = Get-Content -LiteralPath $moduleSource -Raw
Assert-True 'solution imports the VMware SDK' (
    $sourceText -cmatch '\bVMware\.Sdk\.Vcf\.SddcManager\b'
)
Assert-True 'solution resolves SDK operationIds' (
    $sourceText -cmatch '\bGet-VcfSddcManagerOperation\b'
)
foreach ($operationId in @('getDomains', 'getClusters', 'refreshAccessToken')) {
    Assert-True "solution names exact operationId $operationId" (
        $sourceText -cmatch "\b$operationId\b"
    )
}
foreach ($forbidden in @(
    '\bInvoke-WebRequest\b',
    '\bInvoke-RestMethod\b',
    '\bSystem\.Net\.Http\b',
    '\bHttpClient\b',
    '\bWebClient\b',
    '\bTcpClient\b',
    '\bcurl\b',
    '\bwget\b',
    '\bStart-Process\b'
)) {
    Assert-True "solution does not bypass SDK with $forbidden" (
        $sourceText -notmatch $forbidden
    )
}

$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
$sources = Get-Content -LiteralPath $sourcesPath -Raw | ConvertFrom-Json
$sha = '3949fc33339fc5ea1b77eadb258f1cf49aa88e26'
$specPath = 'specifications/sddc-manager/sddc-manager-openapi.json'
$operationIds = 'createToken,refreshAccessToken,getDomains,getClusters'
Assert-Equal 'contract is OpenAPI 3.0.1 derived' '3.0.1' `
    $contract.derived_from.openapi_version
Assert-Equal 'contract is VCF 9.1.0.0 derived' '9.1.0.0' `
    $contract.derived_from.info_version
Assert-Equal 'contract pins repository commit' $sha `
    $contract.derived_from.repository_commit_sha
Assert-Equal 'contract pins exact specification path' $specPath `
    $contract.derived_from.spec_path
Assert-Equal 'contract names exact operationIds' $operationIds `
    (($contract.operations.operationId) -join ',')
Assert-Equal 'contract operation methods' 'POST,PATCH,GET,GET' `
    (($contract.operations.method) -join ',')
Assert-Equal 'official sources repeat every operationId' $operationIds `
    ((@($sources.operationIds)) -join ',')
Assert-Equal 'official sources pin repository commit' $sha `
    $sources.specification.repository_commit_sha
Assert-Equal 'official sources pin specification path' $specPath `
    $sources.specification.spec_path
foreach ($operation in $sources.operations) {
    Assert-Equal "source $($operation.operationId) repeats commit" $sha `
        $operation.repository_commit_sha
    Assert-Equal "source $($operation.operationId) repeats path" $specPath `
        $operation.spec_path
}
Assert-Equal 'getDomains includes every source query parameter' `
    'type,name,vcFqdn,vcInstanceId,isManagementSsoDomain,pageNumber,pageSize,useCache' `
    ((($contract.operations | Where-Object operationId -CEQ 'getDomains').parameters.name) -join ',')
Assert-Equal 'getClusters includes every source query parameter' `
    'isStretched,isImageBased,domainId,managedObjectReferenceId,name,isDefault,isHciMeshEnabled,pageSize,pageNumber,useCache' `
    ((($contract.operations | Where-Object operationId -CEQ 'getClusters').parameters.name) -join ',')
Assert-Equal 'token creation schema keeps all source optional members' `
    'apiKey,idToken,password,username' `
    ((Get-JsonPropertyNames $contract.schemas.TokenCreationSpec.properties) -join ',')

Import-Module 'VMware.Sdk.Vcf.SddcManager' `
    -RequiredVersion '13.5.0.25380678' `
    -Force `
    -ErrorAction Stop
foreach ($operationId in @('getDomains', 'getClusters', 'refreshAccessToken')) {
    $resolved = @(Get-VcfSddcManagerOperation -Name $operationId)
    Assert-Equal "installed SDK resolves $operationId exactly once" 1 $resolved.Count
    Assert-True "installed SDK exposes command for $operationId" (
        $resolved.Count -eq 1 -and $null -ne $resolved[0].CommandInfo
    )
    if ($resolved.Count -eq 1 -and $null -ne $resolved[0].CommandInfo) {
        $installedCommand = Get-Command `
            -Name $resolved[0].CommandInfo.Name `
            -CommandType Cmdlet `
            -ErrorAction Stop
        Assert-Equal "$operationId command comes from genuine SDK" `
            'VMware.Sdk.Vcf.SddcManager' $installedCommand.Source
    }
}

Import-Module $moduleManifest -Force -ErrorAction Stop
$exports = @(
    Get-Command -Module VcfDomainClusterMap -CommandType Function |
        Select-Object -ExpandProperty Name
)
Assert-Equal 'module exports exactly one function' `
    'Get-VcfDomainClusterMap' ($exports -join ',')

$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'vcf91-0010-' + [guid]::NewGuid().ToString('N')
)
$null = New-Item -ItemType Directory -Path $temporaryRoot
$portFile = Join-Path $temporaryRoot 'port.txt'
$requestLog = Join-Path $temporaryRoot 'requests.jsonl'
$runtimeInfoFile = Join-Path $temporaryRoot 'runtime.json'
$serverOut = Join-Path $temporaryRoot 'server.out'
$serverErr = Join-Path $temporaryRoot 'server.err'
$serverProcess = $null

try {
    $serverProcess = Start-Process -FilePath 'python3' -ArgumentList @(
        '-B',
        $mockPath,
        $portFile,
        $requestLog,
        $runtimeInfoFile
    ) -PassThru -RedirectStandardOutput $serverOut -RedirectStandardError $serverErr

    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (
        -not (Test-Path -LiteralPath $portFile -PathType Leaf) -or
        -not (Test-Path -LiteralPath $runtimeInfoFile -PathType Leaf)
    ) {
        if ($serverProcess.HasExited -or [DateTime]::UtcNow -gt $deadline) {
            $detail = Get-Content -LiteralPath $serverErr -Raw `
                -ErrorAction SilentlyContinue
            throw "loopback mock failed to start: $detail"
        }
        Start-Sleep -Milliseconds 40
    }

    $port = [int] (Get-Content -LiteralPath $portFile -Raw).Trim()
    $runtime = Get-Content -LiteralPath $runtimeInfoFile -Raw | ConvertFrom-Json
    $securePassword = ConvertTo-SecureString ([string] $runtime.password) `
        -AsPlainText -Force
    $credential = [pscredential]::new(
        [string] $runtime.username,
        $securePassword
    )
    $connection = Connect-VcfSddcManagerServer `
        -Server '127.0.0.1' `
        -Port $port `
        -Protocol http `
        -Credential $credential `
        -NotDefault `
        -ErrorAction Stop
    $connection = @($connection)[0]
    Assert-True 'genuine SDK connection targets loopback' ($null -ne $connection)

    $beforeInvalid = @(Read-RequestLog -Path $requestLog).Count
    $blankError = $null
    try {
        Get-VcfDomainClusterMap `
            -Server $connection `
            -RefreshTokenId ' ' `
            -PageSize 2 > $null
    } catch {
        $blankError = $_.Exception
    }
    Assert-True 'blank refresh token id is rejected' ($null -ne $blankError)
    Assert-Equal 'blank refresh token id causes no traffic' $beforeInvalid `
        @(Read-RequestLog -Path $requestLog).Count

    $firstRows = @(
        Get-VcfDomainClusterMap `
            -Server $connection `
            -RefreshTokenId ([string] $runtime.refreshTokenId) `
            -PageSize 2 `
            -ErrorAction Stop
    )
    $firstRunRequestCount = @(Read-RequestLog -Path $requestLog).Count
    $secondRows = @(
        Get-VcfDomainClusterMap `
            -Server $connection `
            -RefreshTokenId ([string] $runtime.refreshTokenId) `
            -PageSize 2 `
            -ErrorAction Stop
    )

    Assert-Equal 'first run returns all six domain-cluster rows' 6 $firstRows.Count
    Assert-Equal 'second run returns all six domain-cluster rows' 6 $secondRows.Count
    Assert-Equal 'row projection property order' `
        'domainId,domainName,domainType,clusterId,clusterName,clusterStatus,isDefault,isStretched' `
        (($firstRows[0].PSObject.Properties.Name) -join ',')
    Assert-Equal 'all cluster ids remain unique after refresh' 6 `
        @($firstRows.clusterId | Sort-Object -Unique).Count

    $expectedRows = @(
        foreach ($domain in $runtime.domains) {
            foreach ($cluster in $domain.clusters) {
                [pscustomobject][ordered]@{
                    domainId     = [string] $domain.id
                    domainName   = [string] $domain.name
                    domainType   = [string] $domain.type
                    clusterId    = [string] $cluster.id
                    clusterName  = [string] $cluster.name
                    clusterStatus = [string] $cluster.status
                    isDefault    = [bool] $cluster.isDefault
                    isStretched  = [bool] $cluster.isStretched
                }
            }
        }
    )
    $expectedRows = @(Sort-Ordinal -Values $expectedRows -Keys @(
        'domainName', 'domainId', 'clusterName', 'clusterId'
    ))
    $expectedJson = ConvertTo-Json $expectedRows -Compress -Depth 8
    $firstJson = ConvertTo-Json $firstRows -Compress -Depth 8
    $secondJson = ConvertTo-Json $secondRows -Compress -Depth 8
    Assert-Equal 'first run uses explicit ordinal stable ordering' `
        $expectedJson $firstJson
    Assert-Equal 'flipped second run remains byte-for-byte stable' `
        $firstJson $secondJson

    foreach ($secret in @(
        [string] $runtime.password,
        [string] $runtime.oldAccessToken,
        [string] $runtime.newAccessToken,
        [string] $runtime.refreshTokenId
    )) {
        Assert-True 'result does not expose credentials or tokens' (
            $firstJson -notlike "*$secret*"
        )
    }

    $requests = @(Read-RequestLog -Path $requestLog)
    Assert-Equal 'first run ends after exact resume sequence' 9 $firstRunRequestCount
    Assert-Equal 'exact wire request count across both runs' 14 $requests.Count
    Assert-Equal 'exact operation sequence including expired attempt' `
        'createToken,,getDomains,getDomains,getClusters,getClusters,refreshAccessToken,getClusters,getClusters,getDomains,getDomains,getClusters,getClusters,getClusters' `
        (($requests.operationId | ForEach-Object { [string] $_ }) -join ',')
    Assert-True 'only contract operations plus one SDK bootstrap are served' (
        @($requests | Where-Object {
            $null -eq $_.operationId -and -not [bool] $_.sdkBootstrap
        }).Count -eq 0
    )
    Assert-Equal 'SDK bootstrap occurs exactly once' 1 `
        @($requests | Where-Object { [bool] $_.sdkBootstrap }).Count

    $tokenRequest = @($requests | Where-Object operationId -CEQ 'createToken')
    Assert-Equal 'one createToken call' 1 $tokenRequest.Count
    Assert-Equal 'createToken exact method and target' 'POST /v1/tokens' `
        "$($tokenRequest[0].method) $($tokenRequest[0].rawTarget)"
    Assert-True 'createToken has JSON content type' (
        $tokenRequest[0].contentType -like 'application/json*'
    )
    Assert-Equal 'createToken has no authorization' '' `
        $tokenRequest[0].authorization
    $tokenBody = $tokenRequest[0].body | ConvertFrom-Json
    Assert-Equal 'createToken body omits apiKey and idToken' 'password,username' `
        ((Get-JsonPropertyNames $tokenBody) -join ',')
    Assert-Equal 'createToken carries runtime username' $runtime.username `
        $tokenBody.username
    Assert-Equal 'createToken carries runtime password' $runtime.password `
        $tokenBody.password

    $domainRequests = @($requests | Where-Object operationId -CEQ 'getDomains')
    Assert-Equal 'both runs fetch two complete domain pages' 4 `
        $domainRequests.Count
    Assert-Equal 'exact domain targets omit first-page pageNumber' `
        '/v1/domains?pageSize=2,/v1/domains?pageNumber=2&pageSize=2,/v1/domains?pageSize=2,/v1/domains?pageNumber=2&pageSize=2' `
        (($domainRequests.rawTarget) -join ',')
    Assert-Equal 'domain requests use old then replacement bearer' `
        "Bearer $($runtime.oldAccessToken),Bearer $($runtime.oldAccessToken),Bearer $($runtime.newAccessToken),Bearer $($runtime.newAccessToken)" `
        (($domainRequests.authorization) -join ',')

    $orderedDomains = @(Sort-Ordinal -Values @($runtime.domains) -Keys @('name', 'id'))
    $clusterRequests = @($requests | Where-Object operationId -CEQ 'getClusters')
    Assert-Equal 'cluster requests include one failed identical retry' 7 `
        $clusterRequests.Count
    Assert-Equal 'cluster response statuses expose one mid-run expiry' `
        '200,401,200,200,200,200,200' `
        (($clusterRequests.responseStatus) -join ',')
    $expectedDomainIds = @(
        [string] $orderedDomains[0].id,
        [string] $orderedDomains[1].id,
        [string] $orderedDomains[1].id,
        [string] $orderedDomains[2].id,
        [string] $orderedDomains[0].id,
        [string] $orderedDomains[1].id,
        [string] $orderedDomains[2].id
    )
    Assert-Equal 'domain work queue is sorted and retry preserves position' `
        ($expectedDomainIds -join ',') `
        (($clusterRequests | ForEach-Object { [string] $_.query.domainId[0] }) -join ',')
    Assert-Equal 'completed first domain is not replayed before refresh' 1 `
        @($clusterRequests[0..3] | Where-Object {
            [string] $_.query.domainId[0] -ceq [string] $orderedDomains[0].id
        }).Count
    Assert-Equal 'interrupted domain request is retried exactly once in first run' 2 `
        @($clusterRequests[0..3] | Where-Object {
            [string] $_.query.domainId[0] -ceq [string] $runtime.expiryDomainId
        }).Count
    Assert-Equal 'old token is used through the failed cluster call' `
        "Bearer $($runtime.oldAccessToken),Bearer $($runtime.oldAccessToken)" `
        (($clusterRequests[0..1].authorization) -join ',')
    Assert-Equal 'replacement token is used after refresh and on the second run' `
        ((1..5 | ForEach-Object { "Bearer $($runtime.newAccessToken)" }) -join ',') `
        (($clusterRequests[2..6].authorization) -join ',')

    foreach ($request in @($domainRequests) + @($clusterRequests)) {
        Assert-Equal 'collection GET is bodyless' 0 $request.bodyLength
        Assert-Equal 'collection GET has no content type' '' $request.contentType
        Assert-True 'collection GET accepts JSON' ($request.accept -like '*application/json*')
        Assert-Equal 'collection GET has one Authorization header' 1 `
            @(Get-HeaderValues -Request $request -Name 'authorization').Count
        Assert-True 'collection GET has no empty query value' (
            -not ($request.rawQuery -match '(^|&)[^=]*=(&|$)')
        )
    }
    foreach ($request in $domainRequests) {
        $queryNames = @($request.query.PSObject.Properties.Name)
        Assert-True 'getDomains contains only bound paging members' (
            @($queryNames | Where-Object {
                $_ -cnotin @('pageNumber', 'pageSize')
            }).Count -eq 0
        )
    }
    foreach ($request in $clusterRequests) {
        Assert-Equal 'cluster first page omits pageNumber' 0 `
            @($request.query.PSObject.Properties.Name | Where-Object {
                $_ -ceq 'pageNumber'
            }).Count
        Assert-Equal 'cluster query contains exact bound members' `
            'domainId,pageSize' `
            ((@($request.query.PSObject.Properties.Name) | Sort-Object) -join ',')
        Assert-Equal 'cluster raw target is exact and has no bare delimiter' `
            "/v1/clusters?domainId=$([string] $request.query.domainId[0])&pageSize=2" `
            $request.rawTarget
    }

    $refreshRequest = @(
        $requests | Where-Object operationId -CEQ 'refreshAccessToken'
    )
    Assert-Equal 'refreshAccessToken occurs exactly once' 1 $refreshRequest.Count
    Assert-Equal 'refresh exact method and target' `
        'PATCH /v1/tokens/access-token/refresh' `
        "$($refreshRequest[0].method) $($refreshRequest[0].rawTarget)"
    Assert-True 'refresh uses JSON content type' (
        $refreshRequest[0].contentType -like 'application/json*'
    )
    Assert-Equal 'refresh uses the expired bearer token' `
        "Bearer $($runtime.oldAccessToken)" `
        $refreshRequest[0].authorization
    Assert-Equal 'refresh has exactly one Authorization header' 1 `
        @(Get-HeaderValues -Request $refreshRequest[0] `
            -Name 'authorization').Count
    Assert-Equal 'refresh body is the exact JSON string representation' `
        (([string] $runtime.refreshTokenId | ConvertTo-Json -Compress)) `
        $refreshRequest[0].body
    Assert-Equal 'refresh has no query delimiter' '' $refreshRequest[0].rawQuery

    Assert-Equal 'caller-owned connection retains replacement secret' `
        ([string] $runtime.newAccessToken) ([string] $connection.SessionSecret)
} catch {
    $script:Failures.Add("unexpected verifier error: $($_.Exception.Message)")
    Write-Output "FAIL verifier raised: $($_.Exception.Message)"
    Write-Output $_.ScriptStackTrace
} finally {
    if ($null -ne $serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
        $null = $serverProcess.WaitForExit(5000)
    }
    Remove-Item -LiteralPath $temporaryRoot -Recurse -Force `
        -ErrorAction SilentlyContinue
}

Write-Output "checks=$script:Checks failures=$($script:Failures.Count)"
if ($script:Failures.Count -gt 0) {
    foreach ($failure in $script:Failures) {
        Write-Output "  $failure"
    }
    exit 1
}
Write-Output 'ALL TESTS PASSED'
exit 0
