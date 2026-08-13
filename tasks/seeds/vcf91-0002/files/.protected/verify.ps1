# Protected acceptance verifier for VcfResilientDomains.
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

$moduleManifest = Join-Path $PWD 'VcfResilientDomains/VcfResilientDomains.psd1'
$moduleSource = Join-Path $PWD 'VcfResilientDomains/VcfResilientDomains.psm1'
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

$protectedHashes = @{
    $contractPath = 'aebb0cfe07d8021cc5a8d8f66d810a63048198d86925cd6f57daa351459d5858'
    $sourcesPath = '1a5fd3a21fd468caecbed5dc76d19a864c641f25999f78213e950136f48b2ef8'
    $mockPath = '1b0d133a855abedbdc630ce38f592623cd05c305266f1216467eca59805174fb'
    $moduleManifest = '4818eaea5a04f565e395f4102ea54eeacc2264588529fbcd5f7af5cb8a7c3f3a'
}
foreach ($entry in $protectedHashes.GetEnumerator()) {
    $actual = (Get-FileHash -LiteralPath $entry.Key -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-Equal "protected hash $([IO.Path]::GetFileName($entry.Key))" `
        $entry.Value $actual
}

$sdk = Get-Module -ListAvailable -Name 'VMware.Sdk.Vcf.SddcManager' |
    Where-Object { $_.Version -ge [version] '13.5.0.25380678' } |
    Sort-Object Version -Descending |
    Select-Object -First 1
if ($null -eq $sdk) {
    Write-Output (
        'FAIL prerequisite VMware.Sdk.Vcf.SddcManager ' +
        '>= 13.5.0.25380678 is not installed'
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

$sourceText = Get-Content -LiteralPath $moduleSource -Raw
Assert-True 'solution imports the VMware SDK' (
    $sourceText -cmatch '\bVMware\.Sdk\.Vcf\.SddcManager\b'
)
Assert-True 'solution resolves exact SDK operationIds' (
    $sourceText -cmatch '\bGet-VcfSddcManagerOperation\b'
)
Assert-True 'solution names getDomains' (
    $sourceText -cmatch '\bgetDomains\b'
)
Assert-True 'solution names refreshAccessToken' (
    $sourceText -cmatch '\brefreshAccessToken\b'
)
foreach ($forbidden in @(
    '\bInvoke-WebRequest\b',
    '\bInvoke-RestMethod\b',
    '\bSystem\.Net\.Http\b',
    '\bHttpClient\b',
    '\bWebClient\b',
    '\bWebRequest\b',
    '\bTcpClient\b',
    '\bSockets?\b',
    '\bSystem\.Diagnostics\.Process\b',
    '\bProcessStartInfo\b',
    '\bStart-Process\b',
    '\bAdd-Type\b',
    '\bInvoke-VcfSddcManagerOperation\b',
    '\bSet-VcfSddcManagerOperationParameter\b',
    '\b(?:New|Set)-Alias\b',
    '\b(?:New|Set)-Item\b[^\r\n]*(?:function|alias):',
    (
        '(?im)(?:^|[;&|])\s*(?:&\s*)?(?:[''"][^''"]*[/\\])?' +
            '(?:python3?|pwsh|powershell|bash|sh)(?:[''"])?\b'
    ),
    '\bcurl\b',
    '\bwget\b'
)) {
    Assert-True "solution does not bypass SDK with $forbidden" (
        $sourceText -notmatch $forbidden
    )
}
Assert-True 'solution does not redefine generated SDK commands' (
    $sourceText -notmatch (
        '(?im)^\s*function\s+(?:(?:global|script|local|private):)?' +
        '(?:Get-VcfSddcManagerOperation|Invoke-VcfGetDomains|' +
        'Invoke-VcfRefreshAccessToken)\b'
    )
)

$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
$sources = Get-Content -LiteralPath $sourcesPath -Raw | ConvertFrom-Json
$sha = '3949fc33339fc5ea1b77eadb258f1cf49aa88e26'
$specPath = 'specifications/sddc-manager/sddc-manager-openapi.json'
$operationIds = 'createToken,refreshAccessToken,getDomains'
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
Assert-Equal 'contract operation methods' 'POST,PATCH,GET' `
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
Assert-Equal 'token creation schema keeps all source optional members' `
    'apiKey,idToken,password,username' `
    ((Get-JsonPropertyNames $contract.schemas.TokenCreationSpec.properties) -join ',')

Import-Module 'VMware.Sdk.Vcf.SddcManager' `
    -MinimumVersion '13.5.0.25380678' `
    -Force `
    -ErrorAction Stop
foreach ($operationId in @('getDomains', 'refreshAccessToken')) {
    $resolved = @(Get-VcfSddcManagerOperation -Name $operationId)
    Assert-Equal "installed SDK resolves $operationId exactly once" 1 $resolved.Count
    Assert-True "installed SDK exposes command for $operationId" (
        $resolved.Count -eq 1 -and $null -ne $resolved[0].CommandInfo
    )
    if ($resolved.Count -eq 1 -and $null -ne $resolved[0].CommandInfo) {
        $registered = @(
            Microsoft.PowerShell.Core\Get-Command `
                -Name ([string] $resolved[0].CommandInfo.Name) `
                -Module 'VMware.Sdk.Vcf.SddcManager' `
                -CommandType Cmdlet `
                -All `
                -ErrorAction SilentlyContinue
        )
        Assert-Equal "installed SDK exports one command for $operationId" `
            1 $registered.Count
        if ($registered.Count -eq 1) {
            Assert-Equal "$operationId command comes from genuine SDK" `
                'VMware.Sdk.Vcf.SddcManager' $registered[0].Source
        }
    }
}

Import-Module $moduleManifest -Force -ErrorAction Stop
$exports = @(
    Get-Command -Module VcfResilientDomains -CommandType Function |
        Select-Object -ExpandProperty Name
)
Assert-Equal 'module exports exactly one function' `
    'Export-VcfResilientDomainInventory' ($exports -join ',')

$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'vcf91-0002-' + [guid]::NewGuid().ToString('N')
)
$null = New-Item -ItemType Directory -Path $temporaryRoot
$portFile = Join-Path $temporaryRoot 'port.txt'
$requestLog = Join-Path $temporaryRoot 'requests.jsonl'
$runtimeInfoFile = Join-Path $temporaryRoot 'runtime.json'
$serverOut = Join-Path $temporaryRoot 'server.out'
$serverErr = Join-Path $temporaryRoot 'server.err'
$outputPath = Join-Path $temporaryRoot 'domain-inventory.json'
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
        Export-VcfResilientDomainInventory `
            -Server $connection `
            -RefreshTokenId ' ' `
            -Path $outputPath `
            -PageSize 2 > $null
    } catch {
        $blankError = $_.Exception
    }
    Assert-True 'blank refresh token id is rejected' ($null -ne $blankError)
    Assert-Equal 'blank refresh token id causes no traffic' $beforeInvalid `
        @(Read-RequestLog -Path $requestLog).Count

    $blankTypeError = $null
    try {
        Export-VcfResilientDomainInventory `
            -Server $connection `
            -RefreshTokenId ([string] $runtime.refreshTokenId) `
            -Path $outputPath `
            -PageSize 2 `
            -Type ' ' > $null
    } catch {
        $blankTypeError = $_.Exception
    }
    Assert-True 'bound blank type is rejected' ($null -ne $blankTypeError)
    Assert-Equal 'bound blank type causes no traffic' $beforeInvalid `
        @(Read-RequestLog -Path $requestLog).Count
    Assert-True 'invalid preflight writes no output file' (
        -not (Test-Path -LiteralPath $outputPath)
    )

    $result = Export-VcfResilientDomainInventory `
        -Server $connection `
        -RefreshTokenId ([string] $runtime.refreshTokenId) `
        -Path $outputPath `
        -PageSize 2 `
        -ErrorAction Stop

    Assert-Equal 'result property order' 'Path,Count,RefreshCount' `
        (($result.PSObject.Properties.Name) -join ',')
    Assert-Equal 'result resolves output path' `
        ([IO.Path]::GetFullPath($outputPath)) $result.Path
    Assert-Equal 'result reports every domain' 5 $result.Count
    Assert-Equal 'result reports one refresh' 1 $result.RefreshCount

    Assert-True 'completed export exists' (
        Test-Path -LiteralPath $outputPath -PathType Leaf
    )
    $bytes = [IO.File]::ReadAllBytes($outputPath)
    Assert-True 'export is UTF-8 without BOM' (
        -not (
            $bytes.Length -ge 3 -and
            $bytes[0] -eq 0xEF -and
            $bytes[1] -eq 0xBB -and
            $bytes[2] -eq 0xBF
        )
    )
    Assert-True 'export ends in exactly one LF and no CR' (
        $bytes.Length -ge 2 -and
        $bytes[-1] -eq 0x0A -and
        $bytes[-2] -ne 0x0A -and
        $bytes[-2] -ne 0x0D
    )
    $rawOutput = [Text.Encoding]::UTF8.GetString($bytes)
    Assert-Equal 'export is compact one-line JSON plus LF' 1 `
        (($rawOutput -split "`n").Count - 1)
    $document = $rawOutput | ConvertFrom-Json
    Assert-Equal 'export root has only domains' 'domains' `
        (($document.PSObject.Properties.Name) -join ',')
    $domains = @($document.domains)
    Assert-Equal 'export contains all five domains' 5 $domains.Count
    Assert-Equal 'domain projection property order' `
        'id,name,type,status,isManagementSsoDomain' `
        (($domains[0].PSObject.Properties.Name) -join ',')
    Assert-Equal 'no duplicate domain survived refresh' 5 `
        @($domains.id | Sort-Object -Unique).Count

    $expectedPairs = @(
        $runtime.domains | ForEach-Object {
            [pscustomobject]@{ Name = [string] $_.name; Id = [string] $_.id }
        }
    )
    [Array]::Sort(
        $expectedPairs,
        [System.Comparison[object]] {
            param($left, $right)
            $comparison = [string]::CompareOrdinal($left.Name, $right.Name)
            if ($comparison -ne 0) { return $comparison }
            [string]::CompareOrdinal($left.Id, $right.Id)
        }
    )
    Assert-Equal 'export uses ordinal name/id order' `
        (($expectedPairs | ForEach-Object { "$($_.Name)|$($_.Id)" }) -join ',') `
        (($domains | ForEach-Object { "$($_.name)|$($_.id)" }) -join ',')

    $requests = @(Read-RequestLog -Path $requestLog)
    Assert-Equal 'exact wire request count' 7 $requests.Count
    Assert-Equal 'exact operation sequence including expired attempt' `
        'createToken,,getDomains,getDomains,refreshAccessToken,getDomains,getDomains' `
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
    Assert-Equal 'four getDomains calls include one failed retry' 4 `
        $domainRequests.Count
    Assert-Equal 'exact raw domain targets preserve interrupted page' `
        '/v1/domains?pageSize=2,/v1/domains?pageNumber=2&pageSize=2,/v1/domains?pageNumber=2&pageSize=2,/v1/domains?pageNumber=3&pageSize=2' `
        (($domainRequests.rawTarget) -join ',')
    Assert-Equal 'domain response statuses expose one mid-run expiry' `
        '200,401,200,200' (($domainRequests.responseStatus) -join ',')
    Assert-Equal 'page one is never replayed' 1 `
        @($domainRequests | Where-Object rawTarget -CEQ '/v1/domains?pageSize=2').Count
    Assert-Equal 'failed page is retried exactly once' 2 `
        @($domainRequests | Where-Object rawTarget -CEQ `
            '/v1/domains?pageNumber=2&pageSize=2').Count
    Assert-Equal 'old token is used through the failed call' `
        "Bearer $($runtime.oldAccessToken),Bearer $($runtime.oldAccessToken)" `
        (($domainRequests[0..1].authorization) -join ',')
    Assert-Equal 'new token is used after refresh' `
        "Bearer $($runtime.newAccessToken),Bearer $($runtime.newAccessToken)" `
        (($domainRequests[2..3].authorization) -join ',')
    foreach ($request in $domainRequests) {
        Assert-Equal 'getDomains is bodyless' 0 $request.bodyLength
        Assert-Equal 'getDomains has no content type' '' $request.contentType
        Assert-True 'getDomains accepts JSON' ($request.accept -like '*application/json*')
        Assert-Equal 'getDomains has exactly one Authorization header' 1 `
            @(Get-HeaderValues -Request $request -Name 'authorization').Count
        $queryNames = @($request.query.PSObject.Properties.Name)
        Assert-True 'getDomains query contains only bound paging members' (
            @($queryNames | Where-Object { $_ -cnotin @('pageNumber', 'pageSize') }).Count -eq 0
        )
        Assert-True 'getDomains has no empty query value' (
            -not ($request.rawQuery -match '(^|&)[^=]*=(&|$)')
        )
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

    foreach ($secret in @(
        [string] $runtime.password,
        [string] $runtime.oldAccessToken,
        [string] $runtime.newAccessToken,
        [string] $runtime.refreshTokenId
    )) {
        Assert-True 'result does not expose credentials or tokens' (
            ($result | ConvertTo-Json -Compress) -notlike "*$secret*"
        )
    }
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
