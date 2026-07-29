Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw "ASSERTION FAILED: $Message"
    }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Message)
    if ($Expected -ne $Actual) {
        throw "ASSERTION FAILED: $Message`nExpected: $Expected`nActual:   $Actual"
    }
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$modulePath = Join-Path $root 'VcfNsxPolicyInventory.psm1'
$contractPath = Join-Path $root 'docs/contract.json'
$mockPath = Join-Path $root 'mock_nsx.py'

Assert-True (Test-Path -LiteralPath $modulePath) 'VcfNsxPolicyInventory.psm1 must exist'

$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $modulePath, [ref]$tokens, [ref]$parseErrors)
Assert-Equal 0 @($parseErrors).Count 'the module must parse without errors'
$commandNames = @(
    $ast.FindAll(
        { param($node) $node -is [System.Management.Automation.Language.CommandAst] },
        $true
    ) | ForEach-Object { $_.GetCommandName() }
)
Assert-True ($commandNames -contains 'Import-Module') 'the production path must import the official SDK module'
Assert-True ($commandNames -contains 'Invoke-ListTier1') 'the production path must call Invoke-ListTier1'
Assert-True ($commandNames -contains 'Invoke-ListAllInfraSegments') 'the production path must call Invoke-ListAllInfraSegments'
Assert-True ($commandNames -notcontains 'Invoke-WebRequest') 'the module must not bypass the SDK with Invoke-WebRequest'
Assert-True ($commandNames -notcontains 'Invoke-RestMethod') 'the module must not bypass the SDK with Invoke-RestMethod'

$moduleText = [IO.File]::ReadAllText($modulePath)
Assert-True ($moduleText -match [regex]::Escape('VMware.Sdk.Nsx.Policy')) 'the official VMware.Sdk.Nsx.Policy module must be named'

$vendoredSdk = @(
    Get-ChildItem -LiteralPath $root -Recurse -File |
        Where-Object {
            $_.Name -match '^VMware\.(Sdk\.)?Nsx\.' -or
            $_.Extension -in @('.nupkg', '.dll') -and $_.FullName -match 'VMware'
        }
)
Assert-Equal 0 $vendoredSdk.Count 'no VMware SDK module or binary may be vendored'

$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
Assert-Equal '3949fc33339fc5ea1b77eadb258f1cf49aa88e26' $contract.source.commitSha 'contract commit SHA'
Assert-Equal 'specifications/nsx/openapi-2.0/nsx_policy_api.yaml' $contract.source.specPath 'contract spec path'
Assert-Equal '/policy/api/v1' $contract.source.basePath 'contract base path'
Assert-Equal 'ListTier1,ListAllInfraSegments' (($contract.operations.operationId) -join ',') 'contract operationIds'

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("vcf91-0058-" + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($tempRoot) > $null
$requestLog = Join-Path $tempRoot 'requests.jsonl'
$outOne = Join-Path $tempRoot 'inventory-one.json'
$outTwo = Join-Path $tempRoot 'inventory-two.json'

$process = $null
try {
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = 'python3'
    $start.UseShellExecute = $false
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.ArgumentList.Add('-B')
    $start.ArgumentList.Add($mockPath)
    $start.ArgumentList.Add('--log')
    $start.ArgumentList.Add($requestLog)
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    Assert-True $process.Start() 'the loopback mock must start'
    $readyLine = $process.StandardOutput.ReadLine()
    Assert-True (-not [string]::IsNullOrWhiteSpace($readyLine)) 'the loopback mock must announce its address'
    $ready = $readyLine | ConvertFrom-Json
    Assert-True ($ready.baseUrl -match '^http://127\.0\.0\.1:\d+$') 'the mock must bind only to IPv4 loopback'
    Assert-Equal 'ListTier1,ListAllInfraSegments' (($ready.operationIds) -join ',') 'mock operationIds'

    Import-Module $modulePath -Force
    $module = Get-Module VcfNsxPolicyInventory
    Assert-True ($null -ne $module) 'the implementation module must import'
    $exported = @($module.ExportedFunctions.Keys | Sort-Object)
    $requiredExports = @(
        'Export-VcfNsxPolicyInventory',
        'Get-VcfNsxSegment',
        'Get-VcfNsxTier1',
        'New-VcfNsxPolicySession'
    )
    Assert-Equal ($requiredExports -join ',') ($exported -join ',') 'exact exported function surface'

    $operationPath = @{}
    foreach ($operation in $contract.operations) {
        $operationPath[$operation.operationId] = $operation.path
    }
    $baseUrl = [string]$ready.baseUrl
    $basePath = [string]$contract.source.basePath
    $invocations = [System.Collections.Generic.List[object]]::new()

    $operationInvoker = {
        param([string]$OperationId, [hashtable]$Parameters, $NsxServer)
        if (-not $operationPath.ContainsKey($OperationId)) {
            throw "operation outside contract: $OperationId"
        }
        $query = [System.Collections.Generic.List[string]]::new()
        $query.Add('page_size=' + [uri]::EscapeDataString([string]$Parameters.PageSize))
        if ($Parameters.ContainsKey('Cursor') -and -not [string]::IsNullOrEmpty([string]$Parameters.Cursor)) {
            $query.Add('cursor=' + [uri]::EscapeDataString([string]$Parameters.Cursor))
        }
        $uri = $baseUrl + $basePath + $operationPath[$OperationId] + '?' + ($query -join '&')
        $invocations.Add([pscustomobject]@{
            OperationId = $OperationId
            Cursor = if ($Parameters.ContainsKey('Cursor')) { [string]$Parameters.Cursor } else { $null }
            PageSize = [int]$Parameters.PageSize
            Token = [string]$NsxServer.AccessToken
        })
        $response = Invoke-WebRequest -Uri $uri -Method Get -Headers @{
            Authorization = 'Bearer ' + [string]$NsxServer.AccessToken
            Accept = 'application/json'
        } -SkipHttpErrorCheck
        if ([int]$response.StatusCode -eq 401) {
            throw [System.Net.Http.HttpRequestException]::new(
                'NSX Policy request was unauthorized',
                $null,
                [System.Net.HttpStatusCode]::Unauthorized)
        }
        if ([int]$response.StatusCode -ne 200) {
            throw "mock returned unexpected HTTP $([int]$response.StatusCode)"
        }
        $response.Content | ConvertFrom-Json
    }.GetNewClosure()

    $refreshOneState = [pscustomobject]@{ Count = 0 }
    $refreshOne = {
        param($ExpiredServer)
        $refreshOneState.Count++
        [pscustomobject]@{
            BaseUri = $ExpiredServer.BaseUri
            AccessToken = 'run-one-fresh'
        }
    }.GetNewClosure()
    $sessionOne = New-VcfNsxPolicySession -NsxServer ([pscustomobject]@{
        BaseUri = $baseUrl
        AccessToken = 'run-one-old'
    }) -RefreshConnection $refreshOne -OperationInvoker $operationInvoker
    Export-VcfNsxPolicyInventory -Session $sessionOne -Path $outOne -PageSize 2

    $refreshTwoState = [pscustomobject]@{ Count = 0 }
    $refreshTwo = {
        param($ExpiredServer)
        $refreshTwoState.Count++
        [pscustomobject]@{
            BaseUri = $ExpiredServer.BaseUri
            AccessToken = 'run-two-fresh'
        }
    }.GetNewClosure()
    $sessionTwo = New-VcfNsxPolicySession -NsxServer ([pscustomobject]@{
        BaseUri = $baseUrl
        AccessToken = 'run-two-old'
    }) -RefreshConnection $refreshTwo -OperationInvoker $operationInvoker
    Export-VcfNsxPolicyInventory -Session $sessionTwo -Path $outTwo -PageSize 2

    Assert-Equal 1 $refreshOneState.Count 'first expired access token must refresh exactly once'
    Assert-Equal 1 $refreshTwoState.Count 'second expired access token must refresh exactly once'
    Assert-Equal 'run-one-fresh' $sessionOne.NsxServer.AccessToken 'session must retain first replacement connection'
    Assert-Equal 'run-two-fresh' $sessionTwo.NsxServer.AccessToken 'session must retain second replacement connection'

    $bytesOne = [IO.File]::ReadAllBytes($outOne)
    $bytesTwo = [IO.File]::ReadAllBytes($outTwo)
    Assert-True ($bytesOne.Length -gt 0) 'first export must not be empty'
    Assert-Equal ($bytesOne -join ',') ($bytesTwo -join ',') 'exports must be byte-identical despite flipped responses'
    Assert-True ($bytesOne[0] -ne 0xEF -or $bytesOne[1] -ne 0xBB -or $bytesOne[2] -ne 0xBF) 'export must not have a UTF-8 BOM'
    Assert-Equal 10 $bytesOne[$bytesOne.Length - 1] 'export must end with LF'
    Assert-True (-not (($bytesOne -join ',') -match '13,10')) 'export must not contain CRLF'

    $inventory = [Text.Encoding]::UTF8.GetString($bytesOne) | ConvertFrom-Json
    Assert-Equal 'schemaVersion,tier1Gateways,segments' (($inventory.PSObject.Properties.Name) -join ',') 'top-level JSON key order'
    Assert-Equal 't1-a,t1-b,t1-z,t1-e' (($inventory.tier1Gateways.id) -join ',') 'Tier-1 collection must be ordinal displayName/id sorted'
    Assert-Equal 'seg-a,seg-b,seg-z,seg-d' (($inventory.segments.id) -join ',') 'Segment collection must be ordinal displayName/id sorted'
    Assert-Equal 'id,displayName,path,tier0Path,haMode' (($inventory.tier1Gateways[0].PSObject.Properties.Name) -join ',') 'Tier-1 JSON key order'
    Assert-Equal 'id,displayName,path,connectivityPath,transportZonePath,adminState' (($inventory.segments[0].PSObject.Properties.Name) -join ',') 'Segment JSON key order'
    Assert-Equal $null $inventory.tier1Gateways[0].tier0Path 'JSON null must be preserved for Tier-1'
    Assert-Equal $null $inventory.segments[0].connectivityPath 'JSON null must be preserved for Segment'

    Start-Sleep -Milliseconds 100
    $requests = @(
        Get-Content -LiteralPath $requestLog |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
    Assert-True ($requests.Count -ge 10) 'request log must contain both paged runs and retries'
    Assert-Equal 0 @($requests | Where-Object { $_.path -notin @(
        '/policy/api/v1/infra/tier-1s',
        '/policy/api/v1/infra/segments'
    ) }).Count 'mock may receive only contract paths'
    Assert-Equal 0 @($requests | Where-Object { $_.operationId -notin @(
        'ListTier1',
        'ListAllInfraSegments'
    ) }).Count 'every request must name a contract operationId'
    Assert-Equal 0 @($requests | Where-Object { $_.method -ne 'GET' }).Count 'contract operations must use GET'
    Assert-Equal 0 @($requests | Where-Object { $_.query.page_size[0] -ne '2' }).Count 'page_size must be forwarded on every attempt'
    Assert-Equal 2 @($requests | Where-Object { $_.status -eq 401 }).Count 'one mid-run expiry per export'

    foreach ($prefix in @('run-one', 'run-two')) {
        $expiredIndex = -1
        for ($index = 0; $index -lt $requests.Count; $index++) {
            if ($requests[$index].authorization -eq "Bearer $prefix-old" -and $requests[$index].status -eq 401) {
                $expiredIndex = $index
                break
            }
        }
        Assert-True ($expiredIndex -ge 1) "$prefix must expire after work has already been collected"
        Assert-True ($expiredIndex + 1 -lt $requests.Count) "$prefix must retry after refresh"
        $failed = $requests[$expiredIndex]
        $retried = $requests[$expiredIndex + 1]
        Assert-Equal $failed.operationId $retried.operationId "$prefix retry operationId"
        Assert-Equal $failed.path $retried.path "$prefix retry path"
        Assert-Equal (($failed.query | ConvertTo-Json -Compress)) (($retried.query | ConvertTo-Json -Compress)) "$prefix retry query"
        Assert-Equal "Bearer $prefix-fresh" $retried.authorization "$prefix retry must use refreshed connection"
        Assert-Equal 200 $retried.status "$prefix retry must succeed"
    }

    $expectedSequence = @(
        'ListTier1:',
        'ListTier1:tier1:2',
        'ListTier1:tier1:2',
        'ListAllInfraSegments:',
        'ListAllInfraSegments:segments:2'
    )
    foreach ($prefix in @('run-one', 'run-two')) {
        $actualSequence = @(
            $invocations |
                Where-Object { $_.Token -like "$prefix-*" } |
                ForEach-Object { $_.OperationId + ':' + [string]$_.Cursor }
        )
        Assert-Equal ($expectedSequence -join ',') ($actualSequence -join ',') "$prefix must retain pages and retry the same cursor"
    }

    $forbiddenRefresh = [pscustomobject]@{ Count = 0 }
    $alwaysForbidden = {
        param($OperationId, $Parameters, $NsxServer)
        throw [System.Net.Http.HttpRequestException]::new(
            'forbidden',
            $null,
            [System.Net.HttpStatusCode]::Forbidden)
    }
    $mustNotRefresh = {
        param($ExpiredServer)
        $forbiddenRefresh.Count++
        [pscustomobject]@{ AccessToken = 'should-not-be-used' }
    }.GetNewClosure()
    $forbiddenSession = New-VcfNsxPolicySession -NsxServer ([pscustomobject]@{
        AccessToken = 'forbidden-old'
    }) -RefreshConnection $mustNotRefresh -OperationInvoker $alwaysForbidden
    $forbiddenCaught = $false
    try {
        Get-VcfNsxTier1 -Session $forbiddenSession -PageSize 2 > $null
    }
    catch {
        $forbiddenCaught = $true
        Assert-Equal 403 ([int]$_.Exception.StatusCode) '403 status must propagate'
    }
    Assert-True $forbiddenCaught '403 must fail'
    Assert-Equal 0 $forbiddenRefresh.Count 'non-401 failures must not refresh'

    $deadRefresh = [pscustomobject]@{ Count = 0 }
    $alwaysUnauthorized = {
        param($OperationId, $Parameters, $NsxServer)
        throw [System.Net.Http.HttpRequestException]::new(
            'unauthorized',
            $null,
            [System.Net.HttpStatusCode]::Unauthorized)
    }
    $refreshButStillUnauthorized = {
        param($ExpiredServer)
        $deadRefresh.Count++
        [pscustomobject]@{ AccessToken = 'still-unauthorized-fresh' }
    }.GetNewClosure()
    $deadSession = New-VcfNsxPolicySession -NsxServer ([pscustomobject]@{
        AccessToken = 'never-log-this-old'
    }) -RefreshConnection $refreshButStillUnauthorized -OperationInvoker $alwaysUnauthorized
    $authCaught = $false
    try {
        Get-VcfNsxTier1 -Session $deadSession -PageSize 2 > $null
    }
    catch {
        $authCaught = $true
        Assert-Equal 401 ([int]$_.Exception.StatusCode) 'second 401 must surface as authentication error'
        Assert-True ($_.Exception.Message -notmatch 'never-log-this|still-unauthorized') 'authentication error must not expose token text'
    }
    Assert-True $authCaught 'a second 401 must fail'
    Assert-Equal 1 $deadRefresh.Count 'a failed retry must not refresh twice'

    Write-Output 'ALL TESTS PASSED'
}
finally {
    if ($null -ne $process -and -not $process.HasExited) {
        $process.Kill($true)
        $process.WaitForExit()
    }
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
