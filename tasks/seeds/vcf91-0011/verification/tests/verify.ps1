# Protected acceptance verifier for VcfDomainInventory.
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
$runtimeHome = Join-Path $workspaceRoot '.sandbox-home'

$script:Checks = 0
$script:Failures = [System.Collections.Generic.List[string]]::new()

function Assert-True {
    param([Parameter(Mandatory)][string]$Label, [Parameter(Mandatory)][bool]$Condition)
    $script:Checks++
    if (-not $Condition) {
        $script:Failures.Add($Label)
        Write-Output "FAIL $Label"
    }
}

function Assert-Equal {
    param(
        [Parameter(Mandatory)][string]$Label,
        $Expected,
        $Actual
    )
    $script:Checks++
    if ([string]$Expected -cne [string]$Actual) {
        $script:Failures.Add("$Label (expected <$Expected>, actual <$Actual>)")
        Write-Output "FAIL $Label expected=<$Expected> actual=<$Actual>"
    }
}

function Read-RequestLog {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return @()
    }
    return @(
        Get-Content -LiteralPath $Path |
            Where-Object { $_.Length -gt 0 } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
}

function Get-QueryValue {
    param($Entry, [Parameter(Mandatory)][string]$Name)
    $property = $Entry.query.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return @($property.Value)[0]
}

$moduleManifest = Join-Path $PWD 'VcfDomainInventory.psd1'
$moduleSource = Join-Path $PWD 'VcfDomainInventory.psm1'
$contractPath = Join-Path $PWD 'docs/contract.json'
$sourcesPath = Join-Path $PWD 'docs/official_sources.json'

Assert-True 'module manifest exists' (Test-Path -LiteralPath $moduleManifest -PathType Leaf)
Assert-True 'module source exists' (Test-Path -LiteralPath $moduleSource -PathType Leaf)

$sdk = Get-Module -ListAvailable -Name VMware.Sdk.Vcf.SddcManager |
    Sort-Object Version -Descending |
    Select-Object -First 1
Assert-True 'VMware.Sdk.Vcf.SddcManager prerequisite is installed' ($null -ne $sdk)

$vendoredSdk = @(
    Get-ChildItem -LiteralPath $PWD -Recurse -File |
        Where-Object {
            $_.Name -like 'VMware.Sdk.Vcf*' -or
            $_.FullName -match '[/\\]VMware\.Sdk\.Vcf[^/\\]*[/\\]' -or
            $_.Extension -in @('.dll', '.nupkg')
        }
)
Assert-Equal 'the task does not vendor PowerCLI or generated binaries' 0 $vendoredSdk.Count

$manifestData = Import-PowerShellDataFile -LiteralPath $moduleManifest
$requiredNames = @(
    $manifestData.RequiredModules | ForEach-Object {
        if ($_ -is [string]) { $_ } else { $_.ModuleName }
    }
)
Assert-True 'manifest declares the SDK prerequisite' (
    $requiredNames -ccontains 'VMware.Sdk.Vcf.SddcManager')

$sourceText = Get-Content -LiteralPath $moduleSource -Raw
Assert-True 'production uses Connect-VcfSddcManagerServer' (
    $sourceText -cmatch '\bConnect-VcfSddcManagerServer\b')
Assert-True 'production uses Invoke-VcfGetDomains' (
    $sourceText -cmatch '\bInvoke-VcfGetDomains\b')
Assert-True 'production does not bypass PowerCLI with direct HTTP clients' (
    $sourceText -notmatch
        'Invoke-(RestMethod|WebRequest)|System\.Net\.Http|HttpClient|\bcurl\b')

$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
$sources = Get-Content -LiteralPath $sourcesPath -Raw | ConvertFrom-Json
$operationIds = @($contract.operations | ForEach-Object { $_.operationId })
Assert-Equal 'contract names only the exercised operationIds' `
    'createToken,getDomains' ($operationIds -join ',')
Assert-Equal 'official sources repeat every operationId' `
    'createToken,getDomains' ((@($sources.operationIds)) -join ',')
Assert-Equal 'official source spec path is pinned' `
    'specifications/sddc-manager/sddc-manager-openapi.json' $sources.spec_path
Assert-Equal 'official source repository commit is the pinned VCF 9.1 revision' `
    '3949fc33339fc5ea1b77eadb258f1cf49aa88e26' $sources.repository_commit_sha
Assert-True 'official source is the specification, not an API documentation page' (
    $sources.spec_url -like
        'https://github.com/vmware/vcf-api-specs/blob/*/specifications/sddc-manager/sddc-manager-openapi.json')

Import-Module -Name $moduleManifest -Force -ErrorAction Stop
$exports = @(
    Get-Command -Module VcfDomainInventory -CommandType Function |
        Sort-Object Name |
        ForEach-Object { $_.Name }
)
Assert-Equal 'module exports exactly the two requested functions' `
    'Export-VcfDomainInventory,Get-VcfDomainInventory' ($exports -join ',')

$connectCommand = Get-Command Connect-VcfSddcManagerServer -ErrorAction Stop
$domainsCommand = Get-Command Invoke-VcfGetDomains -ErrorAction Stop
Assert-Equal 'connect command comes from VMware SDK' `
    'VMware.Sdk.Vcf.SddcManager' $connectCommand.ModuleName
Assert-Equal 'domain command comes from VMware SDK' `
    'VMware.Sdk.Vcf.SddcManager' $domainsCommand.ModuleName

$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    'vcf91-0011-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $temporaryRoot
$portFile = Join-Path $temporaryRoot 'port.txt'
$requestLog = Join-Path $temporaryRoot 'requests.jsonl'
$mockStdout = Join-Path $temporaryRoot 'mock.stdout'
$mockStderr = Join-Path $temporaryRoot 'mock.stderr'
$mockScript = Join-Path $PSScriptRoot 'mock_sddc_manager.py'
$serverProcess = $null

try {
    $serverProcess = Start-Process -FilePath 'python3' -ArgumentList @(
        '-B',
        $mockScript,
        $portFile,
        $requestLog,
        $contractPath
    ) -RedirectStandardOutput $mockStdout -RedirectStandardError $mockStderr -PassThru

    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while (
        -not (Test-Path -LiteralPath $portFile -PathType Leaf) -and
        [DateTime]::UtcNow -lt $deadline -and
        -not $serverProcess.HasExited
    ) {
        Start-Sleep -Milliseconds 50
    }
    if (-not (Test-Path -LiteralPath $portFile -PathType Leaf)) {
        $details = if (Test-Path -LiteralPath $mockStderr) {
            Get-Content -LiteralPath $mockStderr -Raw
        } else {
            'no stderr'
        }
        throw "loopback mock did not start: $details"
    }

    $port = [int](Get-Content -LiteralPath $portFile -Raw)
    $securePassword = ConvertTo-SecureString 'fixture-password' -AsPlainText -Force
    $credential = [PSCredential]::new('svc-domain-inventory', $securePassword)
    $connectionArguments = @{
        Server     = '127.0.0.1'
        Port       = $port
        Protocol   = 'http'
        Credential = $credential
    }

    $before = @(Read-RequestLog -Path $requestLog).Count
    $domains = @(Get-VcfDomainInventory @connectionArguments -PageSize 2)
    Assert-Equal 'complete paginated collection contains every domain' 5 $domains.Count
    Assert-Equal 'collection is ordinally sorted by name and id' `
        'Alpha-Mgmt|Bravo-Edge:200|Bravo-Edge:210|Zulu-Compute|alpha-analytics' `
        (($domains | ForEach-Object {
            if ($_.name -ceq 'Bravo-Edge') {
                '{0}:{1}' -f $_.name, $_.id.Substring($_.id.Length - 3)
            } else {
                $_.name
            }
        }) -join '|')
    Assert-Equal 'projection property order is exact' `
        'id,name,type,status,isManagementSsoDomain' `
        (($domains[0].PSObject.Properties.Name) -join ',')
    Assert-True 'boolean projection remains boolean' (
        $domains[0].isManagementSsoDomain -is [bool])

    $pagedLog = @(Read-RequestLog -Path $requestLog)[$before..(
        (Read-RequestLog -Path $requestLog).Count - 1)]
    $pagedGets = @($pagedLog | Where-Object { $_.operationId -ceq 'getDomains' })
    Assert-Equal 'page size two requires exactly three SDK collection calls' 3 $pagedGets.Count
    Assert-True 'first collection request omits pageNumber' (
        $null -eq (Get-QueryValue -Entry $pagedGets[0] -Name 'pageNumber'))
    Assert-Equal 'later collection requests advance from returned page metadata' `
        '2,3' (($pagedGets[1..2] | ForEach-Object {
            Get-QueryValue -Entry $_ -Name 'pageNumber'
        }) -join ',')
    Assert-Equal 'pageSize is forwarded on every page' `
        '2,2,2' (($pagedGets | ForEach-Object {
            Get-QueryValue -Entry $_ -Name 'pageSize'
        }) -join ',')
    Assert-True 'every collection request carries the SDK bearer token' (
        @($pagedGets | Where-Object {
            $_.authorization -cne 'Bearer fixture-access-token'
        }).Count -eq 0)

    $before = @(Read-RequestLog -Path $requestLog).Count
    $viDomains = @(
        Get-VcfDomainInventory @connectionArguments -PageSize 2 -Type 'VI')
    Assert-Equal 'type filter returns all four matching domains across pages' `
        4 $viDomains.Count
    $filterLog = @(Read-RequestLog -Path $requestLog)[$before..(
        (Read-RequestLog -Path $requestLog).Count - 1)]
    $filterGets = @($filterLog | Where-Object { $_.operationId -ceq 'getDomains' })
    Assert-Equal 'type filter is forwarded on every page' `
        'VI,VI' (($filterGets | ForEach-Object {
            Get-QueryValue -Entry $_ -Name 'type'
        }) -join ',')

    $emptyDomains = @(
        Get-VcfDomainInventory @connectionArguments -PageSize 2 `
            -Type 'NO_MATCH')
    Assert-Equal 'an empty collection remains empty' 0 $emptyDomains.Count

    $outOne = Join-Path $temporaryRoot 'inventory-one.json'
    $outTwo = Join-Path $temporaryRoot 'inventory-two.json'
    $before = @(Read-RequestLog -Path $requestLog).Count
    $returnedOne = Export-VcfDomainInventory @connectionArguments `
        -PageSize 100 -Path $outOne
    $returnedTwo = Export-VcfDomainInventory @connectionArguments `
        -PageSize 100 -Path $outTwo
    Assert-Equal 'export returns resolved path' `
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
    Assert-Equal 'export is compact single-line JSON plus LF' `
        1 (($raw -split "`n").Count - 1)
    $document = $raw | ConvertFrom-Json
    Assert-Equal 'export root has only domains key' `
        'domains' (($document.PSObject.Properties.Name) -join ',')
    Assert-Equal 'export preserves the sorted collection' `
        (($domains | ForEach-Object { $_.id }) -join ',') `
        ((@($document.domains) | ForEach-Object { $_.id }) -join ',')

    $exportLog = @(Read-RequestLog -Path $requestLog)[$before..(
        (Read-RequestLog -Path $requestLog).Count - 1)]
    $exportGets = @($exportLog | Where-Object { $_.operationId -ceq 'getDomains' })
    Assert-Equal 'each full-page export makes one collection request' 2 $exportGets.Count
    Assert-True 'mock flipped full collection order between export responses' (
        (@($exportGets[0].responseElementIds) -join ',') -cne
        (@($exportGets[1].responseElementIds) -join ','))

    $allRequests = @(Read-RequestLog -Path $requestLog)
    Assert-True 'mock observed only contract operationIds plus SDK session calls' (
        @($allRequests | Where-Object {
            $_.operationId -notin @('createToken', 'getDomains') -and
            -not (
                ($_.method -ceq 'GET' -and $_.path -ceq '/v1/sddc-manager') -or
                ($_.method -ceq 'DELETE' -and
                    $_.path -ceq '/v1/tokens/refresh-token')
            )
        }).Count -eq 0)
    Assert-True 'each inventory operation authenticates through createToken' (
        @($allRequests | Where-Object {
            $_.operationId -ceq 'createToken'
        }).Count -ge 4)

    $caught = $null
    try {
        Get-VcfDomainInventory @connectionArguments -PageSize 0 > $null
    }
    catch {
        $caught = $_
    }
    Assert-True 'non-positive page size is rejected' ($null -ne $caught)
}
catch {
    $script:Failures.Add("unexpected verifier error: $($_.Exception.Message)")
    Write-Output "FAIL unexpected verifier error: $($_.Exception.Message)"
}
finally {
    if ($null -ne $serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
        $null = $serverProcess.WaitForExit(5000)
    }
    Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
    if (
        $env:HOME -and
        [System.IO.Path]::GetFullPath($env:HOME) -ceq
            [System.IO.Path]::GetFullPath($runtimeHome) -and
        (Test-Path -LiteralPath $runtimeHome)
    ) {
        Remove-Item -LiteralPath $runtimeHome -Recurse -Force `
            -ErrorAction SilentlyContinue
    }
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
