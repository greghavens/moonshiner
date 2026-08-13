# Protected acceptance verifier for VcfHostInventory.
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

$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
$script:Checks = 0
$script:Failures = [System.Collections.Generic.List[string]]::new()

function Assert-True {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][bool]$Condition
    )
    $script:Checks++
    if ($Condition) { return }
    $script:Failures.Add($Label)
    Write-Output "FAIL $Label"
}

function Assert-Eq {
    param(
        [Parameter(Mandatory)][string]$Label,
        $Expected,
        $Actual
    )
    $script:Checks++
    if ([string]$Expected -ceq [string]$Actual) { return }
    $failure = "$Label (expected <$Expected>, actual <$Actual>)"
    $script:Failures.Add($failure)
    Write-Output "FAIL $Label"
    Write-Output "  expected: $Expected"
    Write-Output "  actual:   $Actual"
}

function Read-RequestLog {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return @()
    }
    return @(
        Get-Content -LiteralPath $Path |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
}

function Get-QueryNames {
    param([Parameter(Mandatory)]$Entry)
    $names = [string[]]@($Entry.query.PSObject.Properties.Name)
    [Array]::Sort($names, [StringComparer]::Ordinal)
    return $names
}

function Get-QueryValue {
    param(
        [Parameter(Mandatory)]$Entry,
        [Parameter(Mandatory)][string]$Name
    )
    $property = $Entry.query.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return @($property.Value)[0]
}

function Get-OrdinalHostIds {
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [object[]]$Hosts
    )
    $copy = [object[]]@($Hosts)
    [Array]::Sort(
        $copy,
        [System.Comparison[object]] {
            param($left, $right)
            $byFqdn = [string]::CompareOrdinal(
                [string]$left.fqdn,
                [string]$right.fqdn
            )
            if ($byFqdn -ne 0) { return $byFqdn }
            return [string]::CompareOrdinal(
                [string]$left.id,
                [string]$right.id
            )
        }
    )
    return @($copy | ForEach-Object { $_.id })
}

function Get-LogSlice {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][int]$Start
    )
    $all = @(Read-RequestLog -Path $Path)
    if ($Start -ge $all.Count) { return @() }
    return @($all[$Start..($all.Count - 1)])
}

$moduleManifest = Join-Path $Root 'VcfHostInventory.psd1'
$moduleSource = Join-Path $Root 'VcfHostInventory.psm1'
$contractPath = Join-Path $Root 'docs/contract.json'
$sourcesPath = Join-Path $Root 'docs/official_sources.json'
$mockPath = Join-Path $Root '.protected/mock_sddc_manager.py'
$gitignorePath = Join-Path $Root '.gitignore'

Assert-True 'module manifest exists' (
    Test-Path -LiteralPath $moduleManifest -PathType Leaf)
Assert-True 'module source exists' (
    Test-Path -LiteralPath $moduleSource -PathType Leaf)

# Fail closed if an agent changes any protected fixture it was told not to edit.
$protectedHashes = @{
    $contractPath = 'f1075a21916bae66b509e18e6855709e0bc580cfe50eba51f6c791e1e54e94b8'
    $sourcesPath = 'd7a4af0fb2c68da83ff2294e2a51986b0b79a9d320c585d7a168851ce93ea733'
    $mockPath = 'c5c826c58652253c179d0d9183b043419f3f29da4a84d8164991fb392e836641'
    $moduleManifest = 'c9b7b92546e712d6c92a95d2239c9182d40e35708a3b82a3fd586d6ba58794a4'
    $gitignorePath = '2eab86595eefa9c93d8c44f171b67960bd1fdffe1a31613167fac1908a0708ae'
}
foreach ($entry in $protectedHashes.GetEnumerator()) {
    $actual = (Get-FileHash -LiteralPath $entry.Key -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-Eq "protected hash $([IO.Path]::GetFileName($entry.Key))" `
        $entry.Value $actual
}

$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
$sources = Get-Content -LiteralPath $sourcesPath -Raw | ConvertFrom-Json
$expectedSha = '3949fc33339fc5ea1b77eadb258f1cf49aa88e26'
$expectedSpec = 'specifications/sddc-manager/sddc-manager-openapi.json'
$expectedOperations = 'createToken,getHosts'
Assert-Eq 'contract format' 'focused-openapi-projection-v1' `
    $contract.contract_format
Assert-Eq 'contract pins OpenAPI 3.0.1' '3.0.1' `
    $contract.derived_from.openapi
Assert-Eq 'contract pins VCF 9.1' '9.1.0.0' `
    $contract.derived_from.info_version
Assert-Eq 'contract source commit' $expectedSha `
    $contract.derived_from.repository_commit_sha
Assert-Eq 'contract source path' $expectedSpec `
    $contract.derived_from.spec_path
Assert-Eq 'contract operationIds' $expectedOperations `
    (($contract.operations.operationId) -join ',')
Assert-Eq 'contract methods' 'POST,GET' `
    (($contract.operations.method) -join ',')
Assert-Eq 'contract paths' '/v1/tokens,/v1/hosts' `
    (($contract.operations.path) -join ',')
Assert-Eq 'official source repository commit' $expectedSha `
    $sources.repository.commit_sha
Assert-Eq 'official source specification path' $expectedSpec `
    $sources.specification.path
Assert-Eq 'official source operationIds' $expectedOperations `
    ((@($sources.operationIds)) -join ',')
Assert-Eq 'official source operation records every operationId' `
    $expectedOperations (($sources.operations.operationId) -join ',')
foreach ($operation in $sources.operations) {
    Assert-Eq "source $($operation.operationId) repeats commit" $expectedSha `
        $operation.repository_commit_sha
    Assert-Eq "source $($operation.operationId) repeats path" $expectedSpec `
        $operation.spec_path
}
Assert-True 'official source is a pinned specification URL' (
    $sources.specification.pinned_raw_url -ceq
        "https://raw.githubusercontent.com/vmware/vcf-api-specs/$expectedSha/$expectedSpec")

$expectedQueryParameters = @(
    'pageSize',
    'pageNumber',
    'fqdn',
    'status',
    'domainId',
    'clusterId',
    'networkpoolId',
    'storageType',
    'datastoreName',
    'ipAddressVersionForVmotion',
    'isStandalone',
    'isLifecycleManaged',
    'isVsanWitnessHost',
    'size',
    'page'
)
$getHostsContract = @(
    $contract.operations | Where-Object operationId -CEQ 'getHosts')
Assert-Eq 'contract contains getHosts once' 1 $getHostsContract.Count
Assert-Eq 'getHosts query list is copied in specification order' `
    ($expectedQueryParameters -join ',') `
    (($getHostsContract[0].queryParameters.name) -join ',')
Assert-Eq 'projection key order is pinned' `
    'id,fqdn,status,isStandalone,isLifecycleManaged,isVsanWitnessHost' `
    (($contract.client_policy.projection_key_order) -join ',')

# Require the real VMware SDK surface and reject vendored or parallel clients.
$sdk = Get-Module -ListAvailable -Name VMware.Sdk.Vcf.SddcManager |
    Sort-Object Version -Descending |
    Select-Object -First 1
Assert-True 'VMware SDK prerequisite is installed' ($null -ne $sdk)
Assert-True 'VMware SDK prerequisite is new enough' (
    $null -ne $sdk -and
    $sdk.Version -ge [version]'13.5.0.25380678')

$manifestData = Import-PowerShellDataFile -LiteralPath $moduleManifest
$requiredModules = @($manifestData.RequiredModules)
Assert-Eq 'manifest declares exactly one prerequisite' 1 $requiredModules.Count
$requiredName = if ($requiredModules[0] -is [string]) {
    $requiredModules[0]
} else {
    $requiredModules[0].ModuleName
}
Assert-Eq 'manifest prerequisite is the VMware SDK' `
    'VMware.Sdk.Vcf.SddcManager' $requiredName

$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $moduleSource,
    [ref]$tokens,
    [ref]$parseErrors
)
Assert-Eq 'module parses without errors' 0 @($parseErrors).Count
$commandNames = @(
    $ast.FindAll(
        { param($Node) $Node -is [System.Management.Automation.Language.CommandAst] },
        $true
    ) | ForEach-Object { $_.GetCommandName() } |
        Where-Object { $null -ne $_ }
)
Assert-True 'production invokes the generated getHosts binding' (
    $commandNames -contains 'Invoke-VcfGetHosts')
foreach ($forbidden in @(
    'Invoke-WebRequest',
    'Invoke-RestMethod',
    'curl',
    'curl.exe',
    'wget'
)) {
    Assert-True "module does not call $forbidden" (
        $commandNames -notcontains $forbidden)
}
$sourceText = Get-Content -LiteralPath $moduleSource -Raw
foreach ($forbiddenType in @(
    'System.Net.Http.HttpClient',
    'System.Net.WebRequest',
    'TcpClient',
    'Socket'
)) {
    Assert-True "module does not use $forbiddenType" (
        $sourceText -notmatch [regex]::Escape($forbiddenType))
}
$vendored = @(
    Get-ChildItem -LiteralPath $Root -Recurse -File |
        Where-Object {
            $_.Extension.ToLowerInvariant() -in @(
                '.dll', '.nupkg', '.snupkg', '.zip'
            ) -or $_.Name -like 'VMware.Sdk.Vcf*'
        }
)
Assert-Eq 'no SDK or binary dependency is vendored' 0 $vendored.Count

$runtimeDir = Join-Path $Root '_verification'
$serverProcess = $null
$connection = $null
try {
    New-Item -ItemType Directory -Force -Path $runtimeDir > $null
    $portFile = Join-Path $runtimeDir 'port.txt'
    $logFile = Join-Path $runtimeDir 'requests.jsonl'
    $runtimeInfoFile = Join-Path $runtimeDir 'runtime.json'
    $serverOut = Join-Path $runtimeDir 'server.out'
    $serverErr = Join-Path $runtimeDir 'server.err'
    Remove-Item -LiteralPath @(
        $portFile,
        $logFile,
        $runtimeInfoFile,
        $serverOut,
        $serverErr
    ) -ErrorAction SilentlyContinue

    $serverProcess = Start-Process -FilePath 'python3' `
        -ArgumentList @(
            '-B',
            $mockPath,
            $portFile,
            $logFile,
            $runtimeInfoFile
        ) `
        -PassThru `
        -RedirectStandardOutput $serverOut `
        -RedirectStandardError $serverErr

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

    $port = [int](Get-Content -LiteralPath $portFile -Raw).Trim()
    $runtimeInfo = Get-Content -LiteralPath $runtimeInfoFile -Raw |
        ConvertFrom-Json

    Import-Module 'VMware.Sdk.Vcf.SddcManager' `
        -MinimumVersion '13.5.0.25380678' `
        -Force `
        -ErrorAction Stop
    $connectCommand = Get-Command Connect-VcfSddcManagerServer -ErrorAction Stop
    $hostsCommand = Get-Command Invoke-VcfGetHosts -ErrorAction Stop
    Assert-Eq 'connect command comes from VMware SDK' `
        'VMware.Sdk.Vcf.SddcManager' $connectCommand.Source
    Assert-Eq 'host command comes from VMware SDK' `
        'VMware.Sdk.Vcf.SddcManager' $hostsCommand.Source
    $resolved = @(Get-VcfSddcManagerOperation -Name 'getHosts')
    Assert-Eq 'installed SDK resolves getHosts once' 1 $resolved.Count
    Assert-Eq 'resolved getHosts path matches contract' `
        '/v1/hosts' $resolved[0].Path
    Assert-Eq 'resolved getHosts method matches contract' `
        'GET' ([string]$resolved[0].Method).ToUpperInvariant()

    Import-Module -Name $moduleManifest -Force -ErrorAction Stop
    $exports = @(
        Get-Command -Module VcfHostInventory -CommandType Function |
            Sort-Object Name |
            ForEach-Object { $_.Name }
    )
    Assert-Eq 'module exports exactly the requested functions' `
        'Export-VcfHostInventory,Get-VcfHostInventory' ($exports -join ',')

    $securePassword = ConvertTo-SecureString `
        ([string]$runtimeInfo.password) -AsPlainText -Force
    $connection = Connect-VcfSddcManagerServer `
        -Server '127.0.0.1' `
        -Port $port `
        -Protocol http `
        -User ([string]$runtimeInfo.username) `
        -Password $securePassword `
        -NotDefault `
        -ErrorAction Stop

    $connectionLog = @(Read-RequestLog -Path $logFile)
    Assert-Eq 'SDK connection makes exactly two bootstrap requests' `
        2 $connectionLog.Count
    Assert-Eq 'connection operation sequence' 'createToken,' `
        (($connectionLog.operationId | ForEach-Object { [string]$_ }) -join ',')

    $tokenRequest = @(
        $connectionLog | Where-Object operationId -CEQ 'createToken')
    Assert-Eq 'one createToken request' 1 $tokenRequest.Count
    Assert-Eq 'createToken method' 'POST' $tokenRequest[0].method
    Assert-Eq 'createToken exact target' '/v1/tokens' $tokenRequest[0].rawTarget
    Assert-Eq 'createToken has no query' '' $tokenRequest[0].rawQuery
    Assert-Eq 'createToken carries no bearer token' '' `
        $tokenRequest[0].authorization
    Assert-True 'createToken content type is JSON' (
        $tokenRequest[0].contentType -like 'application/json*')
    $tokenBody = $tokenRequest[0].body | ConvertFrom-Json
    $tokenNames = [string[]]@($tokenBody.PSObject.Properties.Name)
    [Array]::Sort($tokenNames, [StringComparer]::Ordinal)
    Assert-Eq 'createToken omits unset apiKey and idToken fields' `
        'password,username' ($tokenNames -join ',')
    Assert-Eq 'createToken username reaches SDK' `
        $runtimeInfo.username $tokenBody.username
    Assert-Eq 'createToken password reaches SDK' `
        $runtimeInfo.password $tokenBody.password

    $versionProbe = @(
        $connectionLog | Where-Object {
            $null -eq $_.operationId -and
            $_.method -ceq 'GET' -and
            $_.path -ceq '/v1/sddc-manager'
        }
    )
    Assert-Eq 'one SDK-internal version probe' 1 $versionProbe.Count
    Assert-Eq 'version probe exact target' '/v1/sddc-manager' `
        $versionProbe[0].rawTarget
    Assert-Eq 'version probe has no body' 0 $versionProbe[0].bodyLength
    Assert-Eq 'version probe has exact bearer token' `
        "Bearer $($runtimeInfo.accessToken)" $versionProbe[0].authorization

    $connectionArguments = @{ Server = $connection }
    $expectedAllIds = @(Get-OrdinalHostIds -Hosts @($runtimeInfo.hosts))

    $before = @(Read-RequestLog -Path $logFile).Count
    $hosts = @(Get-VcfHostInventory @connectionArguments -PageSize 2)
    Assert-Eq 'complete paginated collection contains every host' `
        $runtimeInfo.hosts.Count $hosts.Count
    Assert-Eq 'collection is ordinally sorted by fqdn then id' `
        ($expectedAllIds -join ',') (($hosts.id) -join ',')
    Assert-Eq 'projection property order is exact' `
        'id,fqdn,status,isStandalone,isLifecycleManaged,isVsanWitnessHost' `
        (($hosts[0].PSObject.Properties.Name) -join ',')
    Assert-True 'boolean values remain booleans' (
        $hosts[0].isStandalone -is [bool] -and
        $hosts[0].isLifecycleManaged -is [bool] -and
        $hosts[0].isVsanWitnessHost -is [bool])

    $pagedLog = @(Get-LogSlice -Path $logFile -Start $before)
    $pagedGets = @(
        $pagedLog | Where-Object operationId -CEQ 'getHosts')
    Assert-Eq 'page size two requires exactly three collection calls' `
        3 $pagedGets.Count
    Assert-Eq 'all collection methods are exact' 'GET,GET,GET' `
        (($pagedGets.method) -join ',')
    Assert-True 'all collection paths are exact' (
        @($pagedGets | Where-Object { $_.path -cne '/v1/hosts' }).Count -eq 0)
    Assert-True 'collection targets contain a query without a dangling delimiter' (
        @($pagedGets | Where-Object {
            $_.rawTarget -notmatch '^/v1/hosts\?.+=.+$' -or
            $_.rawTarget.EndsWith('?')
        }).Count -eq 0)
    Assert-Eq 'first request sends only pageSize' 'pageSize' `
        ((Get-QueryNames -Entry $pagedGets[0]) -join ',')
    Assert-Eq 'later requests send only pageNumber and pageSize' `
        'pageNumber,pageSize|pageNumber,pageSize' `
        (($pagedGets[1..2] | ForEach-Object {
            (Get-QueryNames -Entry $_) -join ','
        }) -join '|')
    Assert-Eq 'first collection request omits pageNumber' '' `
        ([string](Get-QueryValue -Entry $pagedGets[0] -Name 'pageNumber'))
    Assert-Eq 'later page numbers advance from returned metadata' '2,3' `
        (($pagedGets[1..2] | ForEach-Object {
            Get-QueryValue -Entry $_ -Name 'pageNumber'
        }) -join ',')
    Assert-Eq 'pageSize is sent on every request' '2,2,2' `
        (($pagedGets | ForEach-Object {
            Get-QueryValue -Entry $_ -Name 'pageSize'
        }) -join ',')
    Assert-True 'unset optional query fields are absent, not empty' (
        @($pagedGets | Where-Object {
            @($_.query.PSObject.Properties | Where-Object {
                $_.Name -notin @('pageSize', 'pageNumber') -or
                @($_.Value)[0] -ceq ''
            }).Count -gt 0
        }).Count -eq 0)
    Assert-True 'every collection request carries the exact bearer token' (
        @($pagedGets | Where-Object {
            $_.authorization -cne "Bearer $($runtimeInfo.accessToken)"
        }).Count -eq 0)
    Assert-True 'every collection request accepts JSON' (
        @($pagedGets | Where-Object {
            $_.headers.accept -notlike '*application/json*'
        }).Count -eq 0)
    Assert-True 'GET requests carry no body or content type' (
        @($pagedGets | Where-Object {
            $_.bodyLength -ne 0 -or $_.contentType -cne ''
        }).Count -eq 0)

    $assignedFixture = @(
        $runtimeInfo.hosts | Where-Object status -CEQ 'ASSIGNED')
    $expectedAssignedIds = @(Get-OrdinalHostIds -Hosts $assignedFixture)
    $before = @(Read-RequestLog -Path $logFile).Count
    $assigned = @(
        Get-VcfHostInventory @connectionArguments -PageSize 2 `
            -Status 'ASSIGNED')
    Assert-Eq 'status filter returns its complete paginated collection' `
        $assignedFixture.Count $assigned.Count
    Assert-Eq 'filtered collection remains ordinally sorted' `
        ($expectedAssignedIds -join ',') (($assigned.id) -join ',')
    $filterGets = @(
        Get-LogSlice -Path $logFile -Start $before |
            Where-Object operationId -CEQ 'getHosts')
    Assert-Eq 'four assigned hosts require two filtered pages' `
        2 $filterGets.Count
    Assert-Eq 'filter first-page query shape is exact' 'pageSize,status' `
        ((Get-QueryNames -Entry $filterGets[0]) -join ',')
    Assert-Eq 'filter continuation query shape is exact' `
        'pageNumber,pageSize,status' `
        ((Get-QueryNames -Entry $filterGets[1]) -join ',')
    Assert-Eq 'status is forwarded unchanged on every page' `
        'ASSIGNED,ASSIGNED' `
        (($filterGets | ForEach-Object {
            Get-QueryValue -Entry $_ -Name 'status'
        }) -join ',')

    foreach ($invalidCase in @(
        @{ Label = 'zero page size'; Arguments = @{ PageSize = 0 } },
        @{ Label = 'blank supplied status'; Arguments = @{ Status = ' ' } }
    )) {
        $before = @(Read-RequestLog -Path $logFile).Count
        $caught = $null
        $caseArguments = $invalidCase.Arguments
        try {
            Get-VcfHostInventory @connectionArguments @caseArguments > $null
        } catch {
            $caught = $_
        }
        Assert-True "$($invalidCase.Label) is rejected" ($null -ne $caught)
        Assert-Eq "$($invalidCase.Label) sends no request" $before `
            @(Read-RequestLog -Path $logFile).Count
    }

    $outOne = Join-Path $runtimeDir 'inventory-one.json'
    $outTwo = Join-Path $runtimeDir 'inventory-two.json'
    $before = @(Read-RequestLog -Path $logFile).Count
    $returnedOne = Export-VcfHostInventory @connectionArguments `
        -PageSize 100 -Path $outOne
    $returnedTwo = Export-VcfHostInventory @connectionArguments `
        -PageSize 100 -Path $outTwo
    Assert-Eq 'export returns resolved path' `
        ([System.IO.Path]::GetFullPath($outOne)) $returnedOne

    $bytesOne = [System.IO.File]::ReadAllBytes($outOne)
    $bytesTwo = [System.IO.File]::ReadAllBytes($outTwo)
    Assert-True 'repeated exports are byte-identical while response order flips' (
        [System.Linq.Enumerable]::SequenceEqual[byte]($bytesOne, $bytesTwo))
    Assert-True 'export is UTF-8 without BOM' (
        -not (
            $bytesOne.Length -ge 3 -and
            $bytesOne[0] -eq 0xEF -and
            $bytesOne[1] -eq 0xBB -and
            $bytesOne[2] -eq 0xBF
        ))
    Assert-True 'export ends with exactly one LF and no CR' (
        $bytesOne.Length -ge 2 -and
        $bytesOne[-1] -eq 0x0A -and
        $bytesOne[-2] -ne 0x0A -and
        $bytesOne[-2] -ne 0x0D)
    $raw = [System.Text.Encoding]::UTF8.GetString($bytesOne)
    Assert-Eq 'export is compact single-line JSON plus LF' `
        1 (($raw -split "`n").Count - 1)
    $document = $raw | ConvertFrom-Json
    Assert-Eq 'export root has only hosts key' 'hosts' `
        (($document.PSObject.Properties.Name) -join ',')
    Assert-Eq 'export contains the complete stable collection' `
        ($expectedAllIds -join ',') `
        ((@($document.hosts.id)) -join ',')

    $exportGets = @(
        Get-LogSlice -Path $logFile -Start $before |
            Where-Object operationId -CEQ 'getHosts')
    Assert-Eq 'each full-page export makes one getHosts call' `
        2 $exportGets.Count
    Assert-Eq 'each export sends exactly pageSize and no optional filter' `
        'pageSize|pageSize' `
        (($exportGets | ForEach-Object {
            (Get-QueryNames -Entry $_) -join ','
        }) -join '|')
    Assert-True 'mock flipped collection order between export responses' (
        (@($exportGets[0].responseElementIds) -join ',') -cne
        (@($exportGets[1].responseElementIds) -join ','))

    $allRequests = @(Read-RequestLog -Path $logFile)
    Assert-Eq 'exact pre-disconnect wire request count' 9 $allRequests.Count
    Assert-Eq 'mock sees only named OpenAPI operationIds' `
        $expectedOperations `
        ((@(
            $allRequests.operationId |
                Where-Object { $null -ne $_ } |
                Sort-Object -Unique
        )) -join ',')
    Assert-True 'every request stays on the loopback authority' (
        @($allRequests | Where-Object {
            $_.headers.host -cne "127.0.0.1:$port"
        }).Count -eq 0)
} catch {
    $script:Failures.Add("unexpected verifier error: $($_.Exception.Message)")
    Write-Output "FAIL unexpected verifier error: $($_.Exception.Message)"
    Write-Output $_.ScriptStackTrace
} finally {
    if ($null -ne $connection) {
        try {
            Disconnect-VcfSddcManagerServer -Server $connection -Force `
                -ErrorAction SilentlyContinue > $null
        } catch {}
    }
    if ($null -ne $serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $runtimeDir -Recurse -Force `
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
