# Protected acceptance harness for VcfTagInventory.psm1.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture =
    [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture =
    [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'
# The PowerCLI SDK greets every import with a CEIP notice; it is not a result.
$WarningPreference = 'SilentlyContinue'
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

function Get-RequestLog {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return , @() }
    , @(
        Get-Content -LiteralPath $Path |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
}

function Get-Member-OrNull {
    param([Parameter(Mandatory)] [AllowNull()] [object] $InputObject, [string] $Name)
    if ($null -eq $InputObject) { return $null }
    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    $property.Value
}

# Off-contract traffic may omit headers the SDK always sends, so read defensively.
function Get-HeaderValue {
    param([Parameter(Mandatory)] [object] $Record, [Parameter(Mandatory)] [string] $Name)
    $value = Get-Member-OrNull -InputObject $Record.headers -Name $Name
    if ($null -eq $value) { return '' }
    [string] $value
}

function Get-QueryValues {
    param([Parameter(Mandatory)] [object] $Record, [Parameter(Mandatory)] [string] $Name)
    $value = Get-Member-OrNull -InputObject $Record.query -Name $Name
    if ($null -eq $value) { return , @() }
    , @($value)
}

function Get-QueryKeys {
    param([Parameter(Mandatory)] [object] $Record)
    (@($Record.queryKeys) | Sort-Object) -join ','
}

function Format-Rows {
    param([Parameter(Mandatory)] [AllowNull()] [object] $Result)
    (@($Result.Tags) | ForEach-Object { "$($_.CategoryName)/$($_.TagName)" }) -join ' | '
}

$modulePath = Join-Path $PSScriptRoot 'VcfTagInventory.psm1'
if (-not (Test-Path -LiteralPath $modulePath -PathType Leaf)) {
    Write-Output 'FAIL VcfTagInventory.psm1 not found in workspace root'
    exit 1
}

# The PowerCLI SDK is an environment prerequisite, never a fixture of this seed.
foreach ($prerequisite in @(
    @{ Name = 'VMware.Sdk.vSphere'; Version = '13.5.0.25380678' },
    @{ Name = 'VMware.Sdk.vSphereRuntime'; Version = '8.0.2099.24145081' }
)) {
    $installed = Get-Module -ListAvailable -Name $prerequisite.Name |
        Where-Object { $_.Version -ge [version] $prerequisite.Version } |
        Sort-Object Version -Descending |
        Select-Object -First 1
    if ($null -eq $installed) {
        Write-Output (
            "FAIL prerequisite $($prerequisite.Name) >= " +
            "$($prerequisite.Version) is not installed"
        )
        exit 1
    }
}

$source = Get-Content -LiteralPath $modulePath -Raw
foreach ($forbidden in @(
    '\bInvoke-WebRequest\b',
    '\bInvoke-RestMethod\b',
    '\bSystem\.Net\.Http\b',
    '\bHttpClient\b',
    '\bHttpWebRequest\b',
    '\bWebClient\b',
    '\bTcpClient\b',
    '\bSslStream\b',
    '\bcurl\b',
    '\bwget\b'
)) {
    Assert-True "solution does not bypass the VMware SDK with $forbidden" (
        $source -notmatch $forbidden
    )
}
Assert-True 'solution builds its server configuration with the SDK runtime' (
    $source -match '\bNew-vSphereServerConfiguration\b'
)
Assert-True 'solution issues requests through the SDK transport' (
    $source -match '\bInvoke-vSphereApiClient\b'
)
Assert-True 'solution builds the category iteration with the SDK model' (
    $source -match '\bInitialize-VcenterTaggingCategoriesIterationSpec\b'
)
Assert-True 'solution builds the tag iteration with the SDK model' (
    $source -match '\bInitialize-VcenterTaggingTagsIterationSpec\b'
)

# Hidden directories hold tool state such as .git and the sandbox home the
# environment installs PowerCLI into; only the solution's own tree is scanned.
$vendored = @(
    Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            $relative = [IO.Path]::GetRelativePath($PSScriptRoot, $_.FullName)
            $segments = $relative -split '[\\/]'
            (-not ($segments | Where-Object { $_.StartsWith('.') })) -and
            $_.Extension.ToLowerInvariant() -in @('.dll', '.nupkg', '.snupkg', '.zip')
        }
)
Assert-Eq 'solution does not vendor binary dependencies' 0 $vendored.Count

# -- protected specification projection and its provenance -----------------

$contractPath = Join-Path $PSScriptRoot 'docs/contract.json'
$sourcesPath = Join-Path $PSScriptRoot 'docs/official_sources.json'
$mockPath = Join-Path $PSScriptRoot 'mock_vcenter.py'
$expectedProtectedHashes = @{
    $contractPath = '5b78dd2dd22efec6c1c7672176b44bed7dcaf74cffc56680b715c79586ac47f0'
    $sourcesPath  = '14df7c448eec6d5c8fbc6e3e18839f380019bd78ea6d2fade5e37b7bd2ae502e'
    $mockPath     = '1004e30213cef0175900ed24e424be24a469158cff641c82842d42854bba2380'
}
foreach ($entry in $expectedProtectedHashes.GetEnumerator()) {
    $actualHash = (Get-FileHash -LiteralPath $entry.Key -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-Eq "protected file hash $([IO.Path]::GetFileName($entry.Key))" `
        $entry.Value $actualHash
}

$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
$sources = Get-Content -LiteralPath $sourcesPath -Raw | ConvertFrom-Json
$pinnedSha = '85151f6b1bb58f13b6ac0304bfec53904bea085f'
$pinnedSpec = 'specifications/vsphere/openapi/automation/vcenter.yaml'
$excludedSha = '3949fc33339fc5ea1b77eadb258f1cf49aa88e26'

Assert-Eq 'contract is pinned to the 9.0.0.0 specification commit' `
    $pinnedSha $contract.source.commitSha
Assert-Eq 'contract is pinned to the 9.0.0.0 repository tag' `
    '9.0.0.0' $contract.source.repositoryTag
Assert-Eq 'contract projects the vCenter automation specification' `
    $pinnedSpec $contract.source.specPath
Assert-Eq 'contract carries the 9.0.0.0 API version' `
    '9.0.0.0' $contract.source.apiVersion
Assert-Eq 'sources record the same commit' `
    $pinnedSha $sources.specification.repository_commit_sha
Assert-Eq 'sources record the 9.0.0.0 repository tag' `
    '9.0.0.0' $sources.specification.repository_tag
Assert-Eq 'sources record the same specification path' `
    $pinnedSpec $sources.specification.spec_path
Assert-Eq 'sources record the 9.0.0.0 revision of the file' `
    '9.0.0.0' $sources.specification.spec_version
Assert-Eq 'sources name the 9.1 revision as the excluded one' `
    $excludedSha $sources.excluded_revision.repository_commit_sha
Assert-True 'the excluded revision is not the pinned one' (
    $sources.excluded_revision.repository_commit_sha -cne $pinnedSha
)

$expectedOperationIds = @(
    'Cis.Session_create',
    'Cis.Session_delete',
    'Vcenter.Tagging.Categories_list',
    'Vcenter.Tagging.Tags_list'
)
Assert-Eq 'contract names exactly the operations in scope' `
    ($expectedOperationIds -join ',') ((@($contract.operations.operationId) | Sort-Object) -join ',')
Assert-Eq 'sources record provenance for every contract operation' `
    ($expectedOperationIds -join ',') ((@($sources.operations.operationId) | Sort-Object) -join ',')
foreach ($operation in $sources.operations) {
    Assert-Eq "operation $($operation.operationId) records the pinned commit" `
        $pinnedSha $operation.repository_commit_sha
    Assert-Eq "operation $($operation.operationId) records the pinned spec path" `
        $pinnedSpec $operation.spec_path
    Assert-True "operation $($operation.operationId) records a spec JSON pointer" (
        $operation.json_pointer -like '/paths/*'
    )
}
foreach ($operation in $contract.operations) {
    Assert-Eq "contract operation $($operation.operationId) wire path" `
        "/api$($operation.path)" $operation.wirePath
}

Import-Module $modulePath -Force

$exports = @(
    Get-Command -Module VcfTagInventory -CommandType Function |
        Select-Object -ExpandProperty Name
)
Assert-Eq 'module exports exactly one function' 'Get-VcfTagInventory' ($exports -join ',')

foreach ($commandName in @('New-vSphereServerConfiguration', 'Invoke-vSphereApiClient')) {
    $command = Get-Command $commandName -ErrorAction Stop
    Assert-Eq "$commandName comes from the genuine SDK runtime" `
        'VMware.Sdk.vSphereRuntime' $command.Source
}
foreach ($commandName in @(
    'Initialize-VcenterTaggingCategoriesIterationSpec',
    'Initialize-VcenterTaggingTagsIterationSpec'
)) {
    $command = Get-Command $commandName -ErrorAction Stop
    Assert-Eq "$commandName comes from the genuine SDK" 'VMware.Sdk.vSphere' $command.Source
}

# -- fixture expectations --------------------------------------------------

$username = 'administrator@vsphere.local'
$password = 'dummy-vcenter-pass-90'
$categoryPrefix = 'urn:vmomi:InventoryServiceCategory:'
$tagPrefix = 'urn:vmomi:InventoryServiceTag:'

$expectedAllCategories = 'Compliance,backup-policy,os-family,owner,workload-tier'
$expectedAllRows = @(
    'Compliance/SOC2',
    'Compliance/pci-dss',
    'backup-policy/hourly',
    'backup-policy/nightly',
    'os-family/rhel9',
    'os-family/windows2022',
    'owner/app-team',
    'owner/platform-team',
    'workload-tier/Tier-Platinum',
    'workload-tier/tier-gold',
    'workload-tier/tier-silver'
) -join ' | '
$expectedFilteredRows = @(
    'Compliance/SOC2',
    'Compliance/pci-dss',
    'workload-tier/Tier-Platinum',
    'workload-tier/tier-gold',
    'workload-tier/tier-silver'
) -join ' | '

$scratch = Join-Path $PSScriptRoot '_verification'
New-Item -ItemType Directory -Force -Path $scratch > $null
$certFile = Join-Path $scratch 'loopback-cert.pem'
$keyFile = Join-Path $scratch 'loopback-key.pem'
$portFile = Join-Path $scratch 'port.txt'
$requestLog = Join-Path $scratch 'requests.jsonl'
$serverOut = Join-Path $scratch 'server.out'
$serverErr = Join-Path $scratch 'server.err'
Remove-Item -LiteralPath $portFile, $requestLog, $serverOut, $serverErr, `
    $certFile, $keyFile -ErrorAction SilentlyContinue

# A loopback-only certificate for the fixture; the SDK always speaks HTTPS.
$subjectAlternativeName =
    [System.Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
$subjectAlternativeName.AddIpAddress([ipaddress] '127.0.0.1')
$subjectAlternativeName.AddDnsName('localhost')
$rsa = [System.Security.Cryptography.RSA]::Create(2048)
$certificateRequest =
    [System.Security.Cryptography.X509Certificates.CertificateRequest]::new(
        'CN=vcf-taginventory-loopback',
        $rsa,
        [System.Security.Cryptography.HashAlgorithmName]::SHA256,
        [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
$certificateRequest.CertificateExtensions.Add($subjectAlternativeName.Build())
$notBefore = [DateTimeOffset]::UtcNow.AddDays(-1)
$certificate = $certificateRequest.CreateSelfSigned($notBefore, $notBefore.AddYears(5))
Set-Content -LiteralPath $certFile -Value $certificate.ExportCertificatePem() -NoNewline
Set-Content -LiteralPath $keyFile -Value $rsa.ExportPkcs8PrivateKeyPem() -NoNewline

$serverProcess = $null
try {
    $serverProcess = Start-Process -FilePath 'python3' `
        -ArgumentList @($mockPath, $certFile, $keyFile, $portFile, $requestLog) `
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
    $authority = "127.0.0.1:$port"

    $securePassword = ConvertTo-SecureString $password -AsPlainText -Force
    $credential = [pscredential]::new($username, $securePassword)

    # A run that raises is a failure in its own right, and the assertions below
    # cannot be trusted afterwards, so it is reported and the harness stops here.
    try {

    # Run 1: the whole inventory, with no optional iteration or filter property set.
    $whole = Get-VcfTagInventory -Server $authority -Credential $credential -SkipCertificateCheck

    # Run 2: two categories, one item per page, so the filter and the marker both travel.
    $filtered = Get-VcfTagInventory -Server $authority -Credential $credential `
        -CategoryName 'workload-tier', 'Compliance' -PageSize 1 -SkipCertificateCheck

    # Run 3: a category that does not exist has to fail, and still close its session.
    $missingFailed = $false
    $missingMessage = ''
    try {
        $null = Get-VcfTagInventory -Server $authority -Credential $credential `
            -CategoryName 'Compliance', 'no-such-category' -SkipCertificateCheck
    } catch {
        $missingFailed = $true
        $missingMessage = [string] $_.Exception.Message
    }

    # Run 4: repeating run 1 has to produce byte identical output.
    $repeat = Get-VcfTagInventory -Server $authority -Credential $credential -SkipCertificateCheck

    # -- returned shape ----------------------------------------------------

    Assert-Eq 'result key order' 'Server,CategoryCount,TagCount,Categories,Tags' (
        (@($whole.PSObject.Properties.Name)) -join ','
    )
    Assert-Eq 'result reports the server it read' $authority $whole.Server
    Assert-Eq 'the whole run counts every category' 5 $whole.CategoryCount
    Assert-Eq 'the whole run counts every tag' 11 $whole.TagCount
    Assert-Eq 'categories are emitted in a stable order' `
        $expectedAllCategories ((@($whole.Categories)) -join ',')
    Assert-Eq 'the whole collection is emitted in a stable order' `
        $expectedAllRows (Format-Rows $whole)
    Assert-Eq 'tag row key order' 'CategoryName,CategoryId,TagName,TagId,Description' (
        (@($whole.Tags[0].PSObject.Properties.Name)) -join ','
    )
    Assert-Eq 'a row carries the tag identifier from the listing' `
        ($tagPrefix + 'b2c3d4e5-SOC2:GLOBAL') $whole.Tags[0].TagId
    Assert-Eq 'a row carries the category identifier from the listing' `
        ($categoryPrefix + '3c4d5e6f-Compliance:GLOBAL') $whole.Tags[0].CategoryId
    Assert-Eq 'a row carries the description from the listing' `
        'In scope for SOC 2 reporting' $whole.Tags[0].Description
    Assert-Eq 'the last row is the ordinally last tag' `
        ($tagPrefix + '4b5c6d7e-tier-silver:GLOBAL') $whole.Tags[10].TagId

    Assert-Eq 'the filtered run counts only the requested categories' 2 $filtered.CategoryCount
    Assert-Eq 'the filtered run counts only their tags' 5 $filtered.TagCount
    Assert-Eq 'the filtered run orders its categories' `
        'Compliance,workload-tier' ((@($filtered.Categories)) -join ',')
    Assert-Eq 'the filtered collection is emitted in a stable order' `
        $expectedFilteredRows (Format-Rows $filtered)

    Assert-True 'an unknown category name fails the run' $missingFailed
    Assert-True 'the failure names the category that is missing' (
        $missingMessage -clike '*no-such-category*'
    )

    Assert-Eq 'repeating the run produces identical output' `
        ($whole | ConvertTo-Json -Depth 6) ($repeat | ConvertTo-Json -Depth 6)

    # -- wire shape --------------------------------------------------------

    $log = Get-RequestLog $requestLog
    Assert-True 'the loopback mock recorded traffic' ($log.Count -gt 0)

    $offContract = @($log | Where-Object { $null -eq $_.operationId })
    Assert-Eq 'every request stays inside the pinned contract' 0 $offContract.Count
    foreach ($stray in $offContract) {
        Write-Output "  off-contract: $($stray.method) $($stray.rawTarget)"
    }
    Assert-Eq 'the loopback service accepted every request' 0 (
        @($log | Where-Object { $_.responseStatus -notin @(200, 201, 204) }).Count
    )
    Assert-Eq 'every request stays on the loopback authority' 0 (
        @($log | Where-Object { (Get-HeaderValue $_ 'host') -cne $authority }).Count
    )
    Assert-Eq 'every request accepts JSON responses' 0 (
        @($log | Where-Object { (Get-HeaderValue $_ 'accept') -notlike '*application/json*' }).Count
    )
    Assert-Eq 'no optional field is ever sent as an empty query value' 0 (
        @($log | Where-Object { $_.rawQuery -match '(^|&)[^=&]+=(&|$)' }).Count
    )
    Assert-Eq 'only contract paths are touched' 0 (
        @($log | Where-Object {
            $_.path -cnotin @(
                '/api/session',
                '/api/vcenter/tagging/categories',
                '/api/vcenter/tagging/tags'
            )
        }).Count
    )

    $creates = @($log | Where-Object operationId -CEQ 'Cis.Session_create')
    $deletes = @($log | Where-Object operationId -CEQ 'Cis.Session_delete')
    Assert-Eq 'each run opens exactly one session' 4 $creates.Count
    Assert-Eq 'each run closes exactly one session' 4 $deletes.Count
    Assert-Eq 'session creation authenticates with basic_auth' 0 (
        @($creates | Where-Object { $_.authorization -cnotlike 'Basic *' }).Count
    )
    $expectedBasic = 'Basic ' + [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes("${username}:${password}"))
    Assert-Eq 'session creation presents the supplied credential' 0 (
        @($creates | Where-Object { $_.authorization -cne $expectedBasic }).Count
    )
    Assert-Eq 'session creation carries no query and no body' 0 (
        @($creates | Where-Object { $_.rawQuery -cne '' -or $_.bodyLength -ne 0 }).Count
    )
    Assert-Eq 'session creation does not present a session token' 0 (
        @($creates | Where-Object { $null -ne $_.sessionId }).Count
    )
    Assert-Eq 'every later request presents the session token instead of basic_auth' 0 (
        @($log | Where-Object {
            $_.operationId -cne 'Cis.Session_create' -and
            ($null -eq $_.sessionId -or $null -ne $_.authorization)
        }).Count
    )
    Assert-Eq 'no run reuses another run session token' 4 (
        @($log | Where-Object { $null -ne $_.sessionId } |
            Select-Object -ExpandProperty sessionId -Unique).Count
    )

    # Split the log into the four runs on the session each request carries.
    $runs = @(
        foreach ($create in $creates) {
            $token = [string] $create.issuedToken
            , @($log | Where-Object { [string] $_.sessionId -ceq $token })
        }
    )
    Assert-Eq 'the log splits into four runs' 4 $runs.Count

    function Get-RunRequests {
        param([object[]] $Run, [string] $OperationId)
        , @($Run | Where-Object operationId -CEQ $OperationId | Sort-Object sequence)
    }

    function Assert-MarkerChain {
        param([string] $Label, [object[]] $Requests)
        for ($index = 0; $index -lt $Requests.Count; $index++) {
            $markers = Get-QueryValues $Requests[$index] 'marker'
            if ($index -eq 0) {
                Assert-Eq "$Label page 1 carries no marker" 0 $markers.Count
                continue
            }
            Assert-Eq "$Label page $($index + 1) carries one marker" 1 $markers.Count
            Assert-Eq "$Label page $($index + 1) echoes the previous marker" `
                ([string] $Requests[$index - 1].responseMarker) ([string] $markers[0])
        }
        if ($Requests.Count -gt 0) {
            Assert-Eq "$Label stops on the page without a marker" '' (
                [string] $Requests[$Requests.Count - 1].responseMarker
            )
        }
    }

    # Run 1 and run 4: nothing optional is set, so no query string at all.
    foreach ($runIndex in @(0, 3)) {
        $label = "run $($runIndex + 1)"
        $categoryRequests = Get-RunRequests $runs[$runIndex] 'Vcenter.Tagging.Categories_list'
        $tagRequests = Get-RunRequests $runs[$runIndex] 'Vcenter.Tagging.Tags_list'
        Assert-Eq "$label reads the categories in two pages" 2 $categoryRequests.Count
        Assert-Eq "$label reads the tags in three pages" 3 $tagRequests.Count
        Assert-Eq "$label asks for the first category page with an empty query" '' `
            ([string] $categoryRequests[0].rawQuery)
        Assert-Eq "$label asks for the first tag page with an empty query" '' `
            ([string] $tagRequests[0].rawQuery)
        Assert-Eq "$label never sends a names filter" 0 (
            @($runs[$runIndex] | Where-Object { (Get-QueryValues $_ 'names').Count -gt 0 }).Count
        )
        Assert-Eq "$label never sends a page size" 0 (
            @($runs[$runIndex] | Where-Object { (Get-QueryValues $_ 'page_size').Count -gt 0 }).Count
        )
        foreach ($request in @($categoryRequests[1]; $tagRequests[1]; $tagRequests[2])) {
            Assert-Eq "$label continuation query is the marker alone" 'marker' (Get-QueryKeys $request)
        }
        Assert-MarkerChain "$label categories" $categoryRequests
        Assert-MarkerChain "$label tags" $tagRequests
        Assert-Eq "$label reads every category exactly once" '4,1' (
            (@($categoryRequests | ForEach-Object { $_.responseItemCount })) -join ','
        )
        Assert-Eq "$label reads every tag exactly once" '4,4,3' (
            (@($tagRequests | ForEach-Object { $_.responseItemCount })) -join ','
        )
        Assert-Eq "$label closes its session last" 'Cis.Session_delete' (
            [string] (@($runs[$runIndex] | Sort-Object sequence)[-1]).operationId
        )
    }

    # Run 2: the filter travels on the first category page only, the page size on every page.
    $filteredCategories = Get-RunRequests $runs[1] 'Vcenter.Tagging.Categories_list'
    $filteredTags = Get-RunRequests $runs[1] 'Vcenter.Tagging.Tags_list'
    Assert-Eq 'run 2 reads the filtered categories in two pages' 2 $filteredCategories.Count
    Assert-Eq 'run 2 reads the tags in eleven pages' 11 $filteredTags.Count
    Assert-Eq 'run 2 sends the filter and the page size on the first category page' `
        'names,page_size' (Get-QueryKeys $filteredCategories[0])
    Assert-Eq 'run 2 explodes the names filter into repeated keys' `
        'workload-tier,Compliance' ((Get-QueryValues $filteredCategories[0] 'names') -join ',')
    Assert-Eq 'run 2 sends the requested page size on the first category page' `
        '1' ((Get-QueryValues $filteredCategories[0] 'page_size') -join ',')
    Assert-Eq 'run 2 replaces the filter with the marker on the second category page' `
        'marker,page_size' (Get-QueryKeys $filteredCategories[1])
    Assert-Eq 'run 2 keeps the requested page size on the category continuation' `
        '1' ((Get-QueryValues $filteredCategories[1] 'page_size') -join ',')
    Assert-Eq 'run 2 never sends a filter alongside a marker' 0 (
        @($runs[1] | Where-Object {
            (Get-QueryValues $_ 'marker').Count -gt 0 -and (Get-QueryValues $_ 'names').Count -gt 0
        }).Count
    )
    Assert-Eq 'run 2 never filters the tag listing by name' 0 (
        @($filteredTags | Where-Object { (Get-QueryValues $_ 'names').Count -gt 0 }).Count
    )
    Assert-Eq 'run 2 asks for the first tag page with the page size alone' `
        'page_size' (Get-QueryKeys $filteredTags[0])
    Assert-Eq 'run 2 keeps the page size on every tag continuation' 0 (
        @($filteredTags | Select-Object -Skip 1 | Where-Object {
            (Get-QueryKeys $_) -cne 'marker,page_size' -or
            ((Get-QueryValues $_ 'page_size') -join ',') -cne '1'
        }).Count
    )
    Assert-MarkerChain 'run 2 categories' $filteredCategories
    Assert-MarkerChain 'run 2 tags' $filteredTags
    Assert-Eq 'run 2 honours the requested page size' '1,1' (
        (@($filteredCategories | ForEach-Object { $_.responseItemCount })) -join ','
    )
    Assert-Eq 'run 2 reads all eleven tags one page at a time' '1,1,1,1,1,1,1,1,1,1,1' (
        (@($filteredTags | ForEach-Object { $_.responseItemCount })) -join ','
    )

    # Run 3: the missing category is detected before any tag is listed.
    $missingCategories = Get-RunRequests $runs[2] 'Vcenter.Tagging.Categories_list'
    $missingTags = Get-RunRequests $runs[2] 'Vcenter.Tagging.Tags_list'
    Assert-Eq 'run 3 lists the categories once' 1 $missingCategories.Count
    Assert-Eq 'run 3 filters on the existing and missing requested names' `
        'Compliance,no-such-category' (
            (Get-QueryValues $missingCategories[0] 'names') -join ',')
    Assert-Eq 'run 3 sends the filter alone' 'names' (Get-QueryKeys $missingCategories[0])
    Assert-Eq 'run 3 does not list any tag' 0 $missingTags.Count
    Assert-Eq 'run 3 still closes its session' 1 (
        @($runs[2] | Where-Object operationId -CEQ 'Cis.Session_delete').Count
    )

    } catch {
        $script:Checks++
        $script:Failures++
        Write-Output "FAIL Get-VcfTagInventory did not complete against the loopback vCenter"
        Write-Output "  $($_.Exception.Message)"
    }
} finally {
    if ($null -ne $serverProcess -and -not $serverProcess.HasExited) {
        $serverProcess.Kill()
        $serverProcess.WaitForExit(5000) > $null
    }
}

Write-Output "checks: $script:Checks  failures: $script:Failures"
if ($script:Failures -gt 0) { exit 1 }
Write-Output 'PASS'
exit 0
