# Protected acceptance harness for VcfaRegionInventory.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture =
    [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture =
    [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'
Set-Location -LiteralPath $PSScriptRoot

$script:Checks = 0
$script:Failures = 0

function Assert-True {
    param([string] $Label, [bool] $Condition)
    $script:Checks++
    if ($Condition) { return }
    $script:Failures++
    Write-Output "FAIL $Label"
}

function Assert-Eq {
    param([string] $Label, $Expected, $Actual)
    $script:Checks++
    if ("$Expected" -ceq "$Actual") { return }
    $script:Failures++
    Write-Output "FAIL $Label"
    Write-Output "  expected: $Expected"
    Write-Output "  actual:   $Actual"
}

function Assert-Sequence {
    param(
        [string] $Label,
        [string[]] $Expected,
        [string[]] $Actual
    )
    $script:Checks++
    if ($Expected.Count -ne $Actual.Count) {
        $script:Failures++
        Write-Output "FAIL $Label"
        Write-Output "  expected $($Expected.Count) element(s), got $($Actual.Count)"
        return
    }
    for ($index = 0; $index -lt $Expected.Count; $index++) {
        if ($Expected[$index] -ceq $Actual[$index]) { continue }
        $script:Failures++
        Write-Output "FAIL $Label"
        Write-Output "  first difference at index $index"
        Write-Output "  expected: $($Expected[$index])"
        Write-Output "  actual:   $($Actual[$index])"
        return
    }
}

function Get-RequestLog {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return @() }
    @(
        Get-Content -LiteralPath $Path |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
}

function Sort-RegionOrdinal {
    param([object[]] $Regions)
    $list = [System.Collections.Generic.List[object]]::new()
    foreach ($item in $Regions) { $list.Add($item) }
    $comparison = [System.Comparison[object]] {
        param($left, $right)
        $byName = [string]::CompareOrdinal($left.name, $right.name)
        if ($byName -ne 0) { return $byName }
        return [string]::CompareOrdinal($left.id, $right.id)
    }
    $list.Sort($comparison)
    return @($list.ToArray())
}

function Get-FixtureSignature {
    param([object] $Region)
    $supervisors = @($Region.supervisors | ForEach-Object { $_.id }) -join ';'
    $policies = @($Region.storagePolicies) -join ';'
    @(
        $Region.id
        $Region.name
        $Region.status
        $Region.loadBalancerType
        $Region.nsxManager.id
        $supervisors
        $policies
        $Region.cpuCapacityMHz
        $Region.memoryCapacityMiB
    ) -join '|'
}

function Get-ResultSignature {
    param([object] $Region)
    $supervisors = @($Region.SupervisorIds) -join ';'
    $policies = @($Region.StoragePolicies) -join ';'
    @(
        $Region.Id
        $Region.Name
        $Region.Status
        $Region.LoadBalancerType
        $Region.NsxManagerId
        $supervisors
        $policies
        $Region.CpuCapacityMHz
        $Region.MemoryCapacityMiB
    ) -join '|'
}

$manifestPath = Join-Path $PSScriptRoot 'VcfaRegionInventory.psd1'
$modulePath = Join-Path $PSScriptRoot 'VcfaRegionInventory.psm1'
foreach ($required in @($manifestPath, $modulePath)) {
    if (Test-Path -LiteralPath $required -PathType Leaf) { continue }
    Write-Output "FAIL $([IO.Path]::GetFileName($required)) not found in workspace root"
    exit 1
}

# PowerCLI is an environment prerequisite, never a fixture supplied by this seed.
$sdkName = 'VMware.Sdk.Vcf.SddcManager'
$sdkVersion = '13.5.0.25380678'
$sdk = Get-Module -ListAvailable -Name $sdkName |
    Where-Object { $_.Version -ge [version] $sdkVersion } |
    Sort-Object Version -Descending |
    Select-Object -First 1
if ($null -eq $sdk) {
    Write-Output "FAIL prerequisite $sdkName >= $sdkVersion is not installed"
    exit 1
}

$contractPath = Join-Path $PSScriptRoot 'docs/contract.json'
$sourcesPath = Join-Path $PSScriptRoot 'docs/official_sources.json'
$mockPath = Join-Path $PSScriptRoot 'mock_vcfa.py'
$fixturePath = Join-Path $PSScriptRoot 'fixtures/vcfa_regions.json'
$expectedProtectedHashes = [ordered] @{
    $contractPath = '645b2081d0ad698002db1bcb4bf2cdd6449d40ebadbf91fcf3f8b050b3bc931b'
    $sourcesPath  = '01f69a90bbb7db253ab0fe5dade766c172f40c00c164b46d6ad905cf1893ef7d'
    $mockPath     = 'fb2ce74b38c094eed31c647a4ced86a418c89dec9aa81654dfbf451c44adba90'
    $fixturePath  = 'e7df3de371d060ef0c3573375bcb02d90de42567776e7d61e3c52ac03ebcf68a'
}
foreach ($entry in $expectedProtectedHashes.GetEnumerator()) {
    $actualHash = (Get-FileHash -LiteralPath $entry.Key -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-Eq "protected file hash $([IO.Path]::GetFileName($entry.Key))" `
        $entry.Value $actualHash
}

# The contract is a hand transcription of reference documentation, not a spec.
$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
$sources = Get-Content -LiteralPath $sourcesPath -Raw | ConvertFrom-Json
Assert-Eq 'contract source kind is reference documentation' `
    'reference-documentation' $contract.source.kind
Assert-True 'contract states it is not derived from a published specification' (
    $contract.source.statement -clike '*derived from reference documentation, not from a published specification*'
)
Assert-Eq 'contract pins the 9.1.0 Accept version' '9.1.0' $contract.source.acceptVersion
Assert-Eq 'contract names exactly one operation' 'queryRegions' (
    ($contract.operations.operationId) -join ','
)
Assert-Eq 'contract operation method' 'GET' ($contract.operations.method -join ',')
Assert-Eq 'contract operation path' '/cloudapi/v1/regions' (
    $contract.operations.path -join ','
)
Assert-Eq 'contract query parameter order' 'filter,metadata,sortAsc,sortDesc,page,pageSize' (
    ($contract.operations[0].queryParameters.name) -join ','
)
Assert-Eq 'contract marks page and pageSize required' 'False,False,False,False,True,True' (
    ($contract.operations[0].queryParameters.required) -join ','
)
Assert-Eq 'contract pins the documented pageSize maximum' 128 (
    ($contract.operations[0].queryParameters |
        Where-Object { $_.name -ceq 'pageSize' }).maximum
)
Assert-Eq 'contract omission rule covers every optional parameter' `
    'filter,metadata,sortAsc,sortDesc' (
        ($contract.conventions.optionalQueryParameters.appliesTo) -join ','
    )
Assert-Eq 'contract pins the bearer authentication header' 'Authorization' `
    $contract.conventions.authentication.header

Assert-Eq 'sources record the documentation source kind' 'reference-documentation' `
    $sources.source_kind
Assert-Eq 'sources record that no specification is published' $false `
    $sources.specification_available
Assert-True 'sources record at least one page' ($sources.pages.Count -ge 1)
foreach ($page in $sources.pages) {
    $label = $page.url
    Assert-True "source page $label is a developer.broadcom.com xAPIs URL" (
        $page.url -clike 'https://developer.broadcom.com/xapis/provider-infrastructure-apis/*'
    )
    Assert-True "source page $label records what it documents" (
        -not [string]::IsNullOrWhiteSpace($page.documents)
    )
    Assert-Eq "source page $label records the fetch date" '2026-08-11' $page.date_fetched
    $operation = $page.operation
    Assert-True "source page $label records a known operation or none" (
        $null -eq $operation -or $operation -ceq 'queryRegions'
    )
}
Assert-True 'sources cover the Query Regions operation page' (
    @($sources.pages | Where-Object {
        $_.url -ceq 'https://developer.broadcom.com/xapis/provider-infrastructure-apis/latest/cloudapi/v1/regions/get/'
    }).Count -eq 1
)

# The solution must call VCF Automation itself; it must not read the fixtures.
$source = Get-Content -LiteralPath $modulePath -Raw
Assert-True 'solution targets the contract collection path' (
    $source -clike '*/cloudapi/v1/regions*'
)
foreach ($forbidden in @(
    'vcfa_regions',
    'mock_vcfa',
    'urn:vcloud:region:'
)) {
    Assert-True "solution does not reach around the wire via '$forbidden'" (
        $source -notlike "*$forbidden*"
    )
}
$vendored = @(
    Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File |
        Where-Object {
            $_.Extension.ToLowerInvariant() -in @('.dll', '.nupkg', '.snupkg', '.zip')
        }
)
Assert-Eq 'solution does not vendor binary dependencies' 0 $vendored.Count

# The manifest must declare the PowerCLI baseline the environment provides.
$manifest = Import-PowerShellDataFile -LiteralPath $manifestPath
Assert-Eq 'manifest root module' 'VcfaRegionInventory.psm1' $(
    if ($manifest.ContainsKey('RootModule')) { $manifest['RootModule'] } else { '' }
)
Assert-Eq 'manifest exports exactly one function' 'Get-VcfaRegion' $(
    if ($manifest.ContainsKey('FunctionsToExport')) {
        (@($manifest['FunctionsToExport'])) -join ','
    } else { '' }
)
$requiredModules = @(
    if ($manifest.ContainsKey('RequiredModules')) { $manifest['RequiredModules'] }
)
$sdkRequirement = @(
    $requiredModules | Where-Object {
        $_ -is [hashtable] -and $_['ModuleName'] -ceq $sdkName
    }
)
Assert-Eq "manifest requires $sdkName" 1 $sdkRequirement.Count
if ($sdkRequirement.Count -eq 1) {
    Assert-Eq "manifest pins the provided $sdkName version" $sdkVersion `
        ([string] $sdkRequirement[0]['ModuleVersion'])
}

Import-Module $manifestPath -Force
$exports = @(
    Get-Command -Module VcfaRegionInventory -CommandType Function |
        Select-Object -ExpandProperty Name
)
Assert-Eq 'module exports exactly one function' 'Get-VcfaRegion' ($exports -join ',')
Assert-True "the required $sdkName module loaded alongside the solution" (
    $null -ne (Get-Module -Name $sdkName)
)

$fixtureRegions = @(Get-Content -LiteralPath $fixturePath -Raw | ConvertFrom-Json)
$expectedAll = @(Sort-RegionOrdinal -Regions $fixtureRegions)
$expectedReady = @(
    Sort-RegionOrdinal -Regions @(
        $fixtureRegions | Where-Object { $_.status -ceq 'READY' }
    )
)
$expectedError = @(
    Sort-RegionOrdinal -Regions @(
        $fixtureRegions | Where-Object { $_.status -ceq 'ERROR' }
    )
)
$accessToken = 'dummy-vcfa-provider-token-91'

$scratch = Join-Path $PSScriptRoot '_verification'
New-Item -ItemType Directory -Force -Path $scratch > $null
$portFile = Join-Path $scratch 'port.txt'
$requestLog = Join-Path $scratch 'requests.jsonl'
$serverOut = Join-Path $scratch 'server.out'
$serverErr = Join-Path $scratch 'server.err'
Remove-Item -LiteralPath $portFile, $requestLog, $serverOut, $serverErr `
    -ErrorAction SilentlyContinue

$serverProcess = $null
try {
    $serverProcess = Start-Process -FilePath 'python3' `
        -ArgumentList @($mockPath, $portFile, $requestLog) `
        -PassThru `
        -RedirectStandardOutput $serverOut `
        -RedirectStandardError $serverErr
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Path -LiteralPath $portFile -PathType Leaf)) {
        if ($serverProcess.HasExited -or [DateTime]::UtcNow -gt $deadline) {
            $detail = Get-Content -LiteralPath $serverErr -Raw -ErrorAction SilentlyContinue
            throw "loopback mock failed to start: $detail"
        }
        Start-Sleep -Milliseconds 40
    }
    $port = [int] (Get-Content -LiteralPath $portFile -Raw).Trim()
    $apiEndpoint = "http://127.0.0.1:$port"

    # --- Scenario A: no optional parameters bound, whole collection ------------
    $all = @(Get-VcfaRegion -ApiEndpoint $apiEndpoint -AccessToken $accessToken)
    $logA = @(Get-RequestLog -Path $requestLog)
    Assert-Eq 'default page size spans the collection in two requests' 2 $logA.Count
    Assert-Eq 'whole collection is returned' $expectedAll.Count $all.Count
    Assert-Sequence 'whole collection is ordered by name then id, ordinally' `
        @($expectedAll | ForEach-Object { Get-FixtureSignature -Region $_ }) `
        @($all | ForEach-Object { Get-ResultSignature -Region $_ })
    if ($all.Count -gt 0) {
        Assert-Eq 'region property order' (
            'Id,Name,Status,LoadBalancerType,NsxManagerId,SupervisorIds,' +
            'StoragePolicies,CpuCapacityMHz,MemoryCapacityMiB'
        ) (($all[0].PSObject.Properties.Name) -join ',')
    }
    Assert-Eq 'requests walk pages in order' '1,2' (
        ($logA | ForEach-Object { $_.query.page }) -join ','
    )
    Assert-Eq 'requests use the documented maximum page size' '128,128' (
        ($logA | ForEach-Object { $_.query.pageSize }) -join ','
    )
    foreach ($record in $logA) {
        Assert-Eq 'unset optional parameters are omitted entirely' 'page,pageSize' (
            (@($record.queryKeys) -join ',')
        )
    }
    Assert-True 'no request carries an empty optional parameter' (
        @($logA | Where-Object {
            $_.rawQuery -like '*filter=*' -or
            $_.rawQuery -like '*metadata=*' -or
            $_.rawQuery -like '*sortAsc=*' -or
            $_.rawQuery -like '*sortDesc=*'
        }).Count -eq 0
    )

    # --- Scenario B: filter and sortAsc bound, sortDesc and metadata unset -----
    $before = $logA.Count
    $ready = @(
        Get-VcfaRegion -ApiEndpoint $apiEndpoint -AccessToken $accessToken `
            -PageSize 25 -Filter 'status==READY' -SortAsc 'name'
    )
    $logB = @((Get-RequestLog -Path $requestLog) | Select-Object -Skip $before)
    Assert-Eq 'filtered collection is paged to exhaustion' 5 $logB.Count
    Assert-Eq 'filtered collection is complete' $expectedReady.Count $ready.Count
    Assert-Sequence 'filtered collection keeps the client-imposed order' `
        @($expectedReady | ForEach-Object { Get-FixtureSignature -Region $_ }) `
        @($ready | ForEach-Object { Get-ResultSignature -Region $_ })
    Assert-Eq 'filtered requests walk pages in order' '1,2,3,4,5' (
        ($logB | ForEach-Object { $_.query.page }) -join ','
    )
    Assert-Eq 'filtered requests keep the requested page size' '25,25,25,25,25' (
        ($logB | ForEach-Object { $_.query.pageSize }) -join ','
    )
    foreach ($record in $logB) {
        Assert-Eq 'only bound parameters appear, in the contract order' `
            'filter,sortAsc,page,pageSize' ((@($record.queryKeys)) -join ',')
        Assert-Eq 'filter value survives round-tripping' 'status==READY' $record.query.filter
        Assert-Eq 'sortAsc value survives round-tripping' 'name' $record.query.sortAsc
        Assert-True 'filter is percent-encoded on the wire' (
            $record.rawQuery -like '*status%3D%3DREADY*'
        )
    }
    Assert-Eq 'no filtered page is fetched twice' 5 (
        @($logB | ForEach-Object { $_.rawQuery } | Sort-Object -Unique).Count
    )

    # --- Scenario C: empty collection still costs exactly one request ----------
    $before = ($logA.Count + $logB.Count)
    $empty = @(
        Get-VcfaRegion -ApiEndpoint $apiEndpoint -AccessToken $accessToken `
            -Filter 'status==FAILED'
    )
    $logC = @((Get-RequestLog -Path $requestLog) | Select-Object -Skip $before)
    Assert-Eq 'an empty collection is fetched exactly once' 1 $logC.Count
    Assert-Eq 'an empty collection yields no regions' 0 $empty.Count
    Assert-Eq 'the empty query still asks for page 1' '1' $logC[0].query.page
    Assert-Eq 'the empty query binds only filter, page and pageSize' `
        'filter,page,pageSize' ((@($logC[0].queryKeys)) -join ',')

    # --- Scenario D: every optional parameter maps and encodes in order ---------
    $before = ($logA.Count + $logB.Count + $logC.Count)
    $allOptionals = @(
        Get-VcfaRegion -ApiEndpoint "$apiEndpoint/" -AccessToken $accessToken `
            -Filter 'status==ERROR' -MetadataFilter 'ns|key==blue value' `
            -SortAsc 'name+id' -SortDesc 'id/name'
    )
    $logD = @((Get-RequestLog -Path $requestLog) | Select-Object -Skip $before)
    Assert-Eq 'all optional parameters need one page for this collection' 1 $logD.Count
    Assert-Sequence 'all-optional query still returns the projected stable inventory' `
        @($expectedError | ForEach-Object { Get-FixtureSignature -Region $_ }) `
        @($allOptionals | ForEach-Object { Get-ResultSignature -Region $_ })
    Assert-Eq 'all parameters appear in contract order' `
        'filter,metadata,sortAsc,sortDesc,page,pageSize' `
        ((@($logD[0].queryKeys)) -join ',')
    Assert-Eq 'metadata maps and round-trips' 'ns|key==blue value' `
        $logD[0].query.metadata
    Assert-Eq 'sortAsc maps and round-trips' 'name+id' $logD[0].query.sortAsc
    Assert-Eq 'sortDesc maps and round-trips' 'id/name' $logD[0].query.sortDesc
    Assert-True 'metadata reserved characters are percent-encoded' (
        $logD[0].rawQuery -clike '*metadata=ns%7Ckey%3D%3Dblue%20value*'
    )
    Assert-True 'sortAsc reserved characters are percent-encoded' (
        $logD[0].rawQuery -clike '*sortAsc=name%2Bid*'
    )
    Assert-True 'sortDesc reserved characters are percent-encoded' (
        $logD[0].rawQuery -clike '*sortDesc=id%2Fname*'
    )

    # --- Scenario E: both pageSize bounds are enforced client-side --------------
    $before += $logD.Count
    $rangeFailures = @()
    foreach ($invalidPageSize in @(0, 129)) {
        try {
            Get-VcfaRegion -ApiEndpoint $apiEndpoint -AccessToken $accessToken `
                -PageSize $invalidPageSize > $null
        }
        catch {
            $rangeFailures += $_
        }
    }
    $logE = @((Get-RequestLog -Path $requestLog) | Select-Object -Skip $before)
    Assert-Eq 'page sizes below and above 1..128 are rejected' 2 $rangeFailures.Count
    Assert-Eq 'out-of-range page sizes never reach the wire' 0 $logE.Count

    # --- Scenario F: mandatory values reject empty strings before the wire ------
    $emptyValueFailures = @()
    try {
        Get-VcfaRegion -ApiEndpoint '' -AccessToken $accessToken > $null
    }
    catch {
        $emptyValueFailures += $_
    }
    try {
        Get-VcfaRegion -ApiEndpoint $apiEndpoint -AccessToken '' > $null
    }
    catch {
        $emptyValueFailures += $_
    }
    $logF = @((Get-RequestLog -Path $requestLog) | Select-Object -Skip $before)
    Assert-Eq 'empty mandatory inputs are rejected' 2 $emptyValueFailures.Count
    Assert-Eq 'empty mandatory inputs never reach the wire' 0 $logF.Count

    # --- Scenario G: a rejected token surfaces the contract error shape --------
    $authFailure = $null
    try {
        Get-VcfaRegion -ApiEndpoint $apiEndpoint `
            -AccessToken 'dummy-vcfa-wrong-token-91' > $null
    }
    catch {
        $authFailure = $_.Exception
    }
    $logG = @((Get-RequestLog -Path $requestLog) | Select-Object -Skip $before)
    Assert-True 'a rejected token throws' ($null -ne $authFailure)
    if ($null -ne $authFailure) {
        Assert-Eq 'rejected token exception type' 'VcfaRegionQueryException' `
            $authFailure.GetType().Name
        Assert-Eq 'rejected token status code' 401 $authFailure.StatusCode
        Assert-Eq 'rejected token minor error code' 'UNAUTHORIZED' `
            $authFailure.MinorErrorCode
        Assert-True 'rejected token exception includes the Error body detail' (
            $authFailure.Message -clike '*A bearer access token is required.*'
        )
        foreach ($secret in @($accessToken, 'dummy-vcfa-wrong-token-91')) {
            Assert-True 'the exception message does not reveal a token' (
                $authFailure.Message -notlike "*$secret*"
            )
        }
    }
    Assert-Eq 'a rejected token stops after one request' 1 $logG.Count
    Assert-Eq 'the rejected request was answered 401' 401 $logG[0].responseStatus

    # --- Scenario H: ApiVersion maps to Accept and non-200 still uses Error -----
    $before += $logG.Count
    $versionFailure = $null
    try {
        Get-VcfaRegion -ApiEndpoint $apiEndpoint -AccessToken $accessToken `
            -ApiVersion '9.0.0' > $null
    }
    catch {
        $versionFailure = $_.Exception
    }
    $logH = @((Get-RequestLog -Path $requestLog) | Select-Object -Skip $before)
    Assert-Eq 'custom API version sends exactly one request' 1 $logH.Count
    Assert-Eq 'custom API version maps to the Accept header' `
        'application/json;version=9.0.0' (($logH[0].accept -replace '\s', ''))
    Assert-True 'custom API version non-200 throws' ($null -ne $versionFailure)
    if ($null -ne $versionFailure) {
        Assert-Eq 'custom API version exception type' 'VcfaRegionQueryException' `
            $versionFailure.GetType().Name
        Assert-Eq 'custom API version status code' 406 $versionFailure.StatusCode
        Assert-Eq 'custom API version minor error code' 'UNSUPPORTED_VERSION' `
            $versionFailure.MinorErrorCode
        Assert-True 'custom API version exception includes the Error body detail' (
            $versionFailure.Message -clike `
                '*The Accept header must request application/json;version=9.1.0.*'
        )
        Assert-True 'custom API version exception does not reveal the token' (
            $versionFailure.Message -notlike "*$accessToken*"
        )
    }

    # --- Wire shape shared by every request -----------------------------------
    $log = @(Get-RequestLog -Path $requestLog)
    Assert-Eq 'total wire request count' 11 $log.Count
    Assert-Eq 'every request matches the single contract operation' 'queryRegions' (
        (@($log.operationId | Sort-Object -Unique)) -join ','
    )
    Assert-True 'no request escapes the contract path' (
        @($log | Where-Object { $_.path -cne '/cloudapi/v1/regions' }).Count -eq 0
    )
    Assert-True 'every request is a GET' (
        @($log | Where-Object { $_.method -cne 'GET' }).Count -eq 0
    )
    Assert-True 'no collection query carries a body' (
        @($log | Where-Object { $_.bodyLength -ne 0 }).Count -eq 0
    )
    Assert-True 'every request stays on the loopback authority' (
        @($log | Where-Object { $_.headers.host -cne "127.0.0.1:$port" }).Count -eq 0
    )
    # HTTP normalises optional whitespace after the parameter separator. The
    # final request intentionally overrides the default and was checked above.
    $defaultVersionRequests = @($log | Select-Object -First 10)
    Assert-True 'every default-version request pins 9.1.0 through Accept' (
        @($defaultVersionRequests | Where-Object {
            ($_.accept -replace '\s', '') -cne 'application/json;version=9.1.0'
        }).Count -eq 0
    )
    Assert-True 'every request target begins with the contract path' (
        @($log | Where-Object {
            -not $_.rawTarget.StartsWith(
                '/cloudapi/v1/regions?', [System.StringComparison]::Ordinal)
        }).Count -eq 0
    )
    $validTokenRequests = @($log | Where-Object { $_.responseStatus -ne 401 })
    Assert-True 'every valid-token request carries the bearer token' (
        @($validTokenRequests | Where-Object {
            $_.authorization -cne "Bearer $accessToken"
        }).Count -eq 0
    )
}
catch {
    $script:Failures++
    Write-Output "FAIL verifier setup or execution: $($_.Exception.Message)"
    if ($_.ScriptStackTrace) { Write-Output $_.ScriptStackTrace }
}
finally {
    if ($null -ne $serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
        $serverProcess.WaitForExit()
    }
}

if ($script:Failures -gt 0) {
    Write-Output "FAILED: $script:Failures failure(s), $script:Checks checks"
    exit 1
}
Write-Output "ALL TESTS PASSED ($script:Checks checks)"
