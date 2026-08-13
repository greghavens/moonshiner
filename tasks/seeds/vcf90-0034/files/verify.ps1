# Protected acceptance harness for VcfTagSync.psm1.
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

function Assert-Throws {
    param(
        [string] $Label,
        [Parameter(Mandatory)] [scriptblock] $Action
    )
    $script:Checks++
    try {
        & $Action > $null
    }
    catch {
        return
    }
    $script:Failures++
    Write-Output "FAIL $Label"
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

function Get-JsonPropertyNames {
    param([Parameter(Mandatory)] [object] $InputObject)
    @($InputObject.PSObject.Properties.Name | Sort-Object)
}

# Off-contract traffic may omit headers the SDK always sends, so read defensively.
function Get-HeaderValue {
    param([Parameter(Mandatory)] [object] $Record, [Parameter(Mandatory)] [string] $Name)
    $property = $Record.headers.PSObject.Properties[$Name]
    if ($null -eq $property) { return '' }
    [string] $property.Value
}

$modulePath = Join-Path $PSScriptRoot 'VcfTagSync.psm1'
if (-not (Test-Path -LiteralPath $modulePath -PathType Leaf)) {
    Write-Output 'FAIL VcfTagSync.psm1 not found in workspace root'
    exit 1
}

# PowerCLI is an environment prerequisite, never a fixture supplied by this seed.
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
Assert-True 'solution builds the tag create spec with the SDK model' (
    $source -match '\bInitialize-CisTaggingTagCreateSpec\b'
)
Assert-True 'solution builds object identifiers with the SDK model' (
    $source -match '\bInitialize-VapiStdDynamicID\b'
)

$vendored = @(
    Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File |
        Where-Object {
            $_.Extension.ToLowerInvariant() -in @(
                '.dll', '.nupkg', '.snupkg', '.zip'
            )
        }
)
Assert-Eq 'solution does not vendor binary dependencies' 0 $vendored.Count

# Verify the protected OpenAPI projection and its per-operation provenance.
$contractPath = Join-Path $PSScriptRoot 'docs/contract.json'
$sourcesPath = Join-Path $PSScriptRoot 'docs/official_sources.json'
$mockPath = Join-Path $PSScriptRoot 'mock_vcenter.py'
$planPath = Join-Path $PSScriptRoot 'tagplan.json'
$gitignorePath = Join-Path $PSScriptRoot '.gitignore'
$expectedProtectedHashes = @{
    $contractPath = '6aa84511c4fcf1797e88d81374652bcb492c1630f5876d5656c95b41c0726e47'
    $sourcesPath = 'bc14ee6fc95c8fc4d2c6324ec83d44807784933cd20aca867fe7230df0972894'
    $mockPath = '7c60f782cbfd74577d6801fcf3f6e621777f0e91568bf1e2b8fe5a17e8eb4be8'
    $planPath = '25ca76cc6cdcc26201ff85fe1af73a6944e0ed79cc80d2cde18ca61755ff0144'
    $gitignorePath = '738071edfa785ba6aa37c6bfa4a1f87045a83540ee387dd84e029bafa0f95522'
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

Assert-Eq 'contract is pinned to the 9.0.0.0 specification commit' `
    $pinnedSha $contract.source.commitSha
Assert-Eq 'contract projects the vCenter automation specification' `
    $pinnedSpec $contract.source.specPath
Assert-Eq 'contract carries the 9.0.0.0 API version' `
    '9.0.0.0' $contract.source.apiVersion
Assert-Eq 'sources record the same commit' `
    $pinnedSha $sources.specification.repository_commit_sha
Assert-Eq 'sources record the same specification path' `
    $pinnedSpec $sources.specification.spec_path
Assert-Eq 'sources record the 9.0.0.0 revision' `
    '9.0.0.0' $sources.specification.spec_version
Assert-Eq 'sources record the 9.0.0.0 repository tag' `
    '9.0.0.0' $sources.specification.repository_tag

$contractOperationIds = @($contract.operations.operationId | Sort-Object)
$sourceOperationIds = @($sources.operations.operationId | Sort-Object)
$expectedOperationIds = @(
    'Cis.Session_create',
    'Cis.Session_delete',
    'Cis.Tagging.Category_get',
    'Cis.Tagging.Category_list',
    'Cis.Tagging.Tag_create',
    'Cis.Tagging.Tag_get',
    'Cis.Tagging.Tag_listTagsForCategory',
    'Cis.Tagging.TagAssociation_attachTagToMultipleObjects',
    'Vcenter.VM_list'
)
Assert-Eq 'contract names exactly the operations in scope' `
    ($expectedOperationIds -join ',') ($contractOperationIds -join ',')
Assert-Eq 'sources record provenance for every contract operation' `
    ($expectedOperationIds -join ',') ($sourceOperationIds -join ',')
foreach ($operation in $sources.operations) {
    Assert-Eq "operation $($operation.operationId) records the pinned commit" `
        $pinnedSha $operation.repository_commit_sha
    Assert-True "operation $($operation.operationId) records a spec JSON pointer" (
        $operation.json_pointer -like '/paths/*'
    )
}

Import-Module $modulePath -Force

$exports = @(
    Get-Command -Module VcfTagSync -CommandType Function |
        Select-Object -ExpandProperty Name
)
Assert-Eq 'module exports exactly one function' `
    'Invoke-VcfTagSync' ($exports -join ',')

foreach ($commandName in @(
    'New-vSphereServerConfiguration',
    'Invoke-vSphereApiClient'
)) {
    $command = Get-Command $commandName -ErrorAction Stop
    Assert-Eq "$commandName comes from the genuine SDK runtime" `
        'VMware.Sdk.vSphereRuntime' $command.Source
}
foreach ($commandName in @(
    'Initialize-CisTaggingTagCreateSpec',
    'Initialize-VapiStdDynamicID'
)) {
    $command = Get-Command $commandName -ErrorAction Stop
    Assert-Eq "$commandName comes from the genuine SDK" `
        'VMware.Sdk.vSphere' $command.Source
}

$username = 'administrator@vsphere.local'
$password = 'dummy-vcenter-pass-90'
$firstToken = 'dummy-vcenter-session-1'
$secondToken = 'dummy-vcenter-session-2'
$categoryId = 'urn:vmomi:InventoryServiceCategory:2c3a4e62-workload-tier:GLOBAL'
$goldTagId = 'urn:vmomi:InventoryServiceTag:3d4b5f73-tier-gold:GLOBAL'
$legacyTagId = 'urn:vmomi:InventoryServiceTag:4e5c6a84-legacy-tier:GLOBAL'
$silverTagId = 'urn:vmomi:InventoryServiceTag:6a7e8c06-tier-silver:GLOBAL'
$bronzeTagId = 'urn:vmomi:InventoryServiceTag:7b8f9d17-tier-bronze:GLOBAL'
$listedCategoryIds = @(
    'urn:vmomi:InventoryServiceCategory:0a1e2c40-os-family:GLOBAL',
    'urn:vmomi:InventoryServiceCategory:1b2f3d51-backup-policy:GLOBAL',
    $categoryId
)
$planVmNames = @(
    'app-01', 'app-02', 'app-03', 'batch-01', 'batch-02', 'web-01', 'web-02'
)

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
        'CN=vcf-tagsync-loopback',
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
            $detail = Get-Content -LiteralPath $serverErr -Raw `
                -ErrorAction SilentlyContinue
            throw "loopback mock failed to start: $detail"
        }
        Start-Sleep -Milliseconds 40
    }
    $port = [int] (Get-Content -LiteralPath $portFile -Raw).Trim()

    $securePassword = ConvertTo-SecureString $password -AsPlainText -Force
    $credential = [pscredential]::new($username, $securePassword)

    $result = Invoke-VcfTagSync `
        -Server "127.0.0.1:$port" `
        -Credential $credential `
        -PlanPath $planPath `
        -SkipCertificateCheck

    Assert-Eq 'result key order' `
        'Server,CategoryId,TagsCreated,VmsResolved,AttachedCount,SessionRefreshCount' (
        ($result.PSObject.Properties.Name) -join ','
    )
    Assert-Eq 'result server' "127.0.0.1:$port" $result.Server
    Assert-Eq 'result resolves the plan category' $categoryId $result.CategoryId
    Assert-Eq 'result reports only the missing tags as created' `
        'tier-bronze,tier-silver' ((@($result.TagsCreated) | Sort-Object) -join ',')
    Assert-Eq 'result resolves every planned virtual machine' 7 $result.VmsResolved
    Assert-Eq 'result attaches every planned assignment' 3 $result.AttachedCount
    Assert-Eq 'result refreshes the expired session exactly once' 1 `
        $result.SessionRefreshCount

    $log = Get-RequestLog $requestLog
    Assert-True 'the loopback mock recorded traffic' ($log.Count -gt 0)

    $offContract = @($log | Where-Object { $null -eq $_.operationId })
    Assert-Eq 'every request stays inside the pinned contract' 0 $offContract.Count
    if ($offContract.Count -gt 0) {
        foreach ($stray in $offContract) {
            Write-Output "  off-contract: $($stray.method) $($stray.rawTarget)"
        }
    }
    Assert-True 'every request remains on the loopback authority' (
        @($log | Where-Object {
            (Get-HeaderValue $_ 'host') -cne "127.0.0.1:$port"
        }).Count -eq 0
    )
    Assert-True 'every contract request accepts JSON responses' (
        @($log | Where-Object {
            $null -ne $_.operationId -and
            (Get-HeaderValue $_ 'accept') -notlike '*application/json*'
        }).Count -eq 0
    )

    # -- session lifecycle -------------------------------------------------

    $sessionCreates = @($log | Where-Object operationId -CEQ 'Cis.Session_create')
    Assert-Eq 'exactly one initial session and one refresh' 2 $sessionCreates.Count
    Assert-Eq 'both session creations are accepted' '201,201' (
        ($sessionCreates.responseStatus) -join ','
    )
    Assert-Eq 'both session creations target the contract path' `
        '/api/session,/api/session' (($sessionCreates.rawTarget) -join ',')
    Assert-True 'session creation authenticates with basic_auth' (
        @($sessionCreates | Where-Object {
            $_.authorization -notlike 'Basic *'
        }).Count -eq 0
    )
    $expectedBasic = 'Basic ' + [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes("${username}:${password}"))
    Assert-True 'session creation sends the supplied credential' (
        @($sessionCreates | Where-Object {
            $_.authorization -cne $expectedBasic
        }).Count -eq 0
    )
    Assert-True 'session creation never presents an existing session token' (
        @($sessionCreates | Where-Object { $null -ne $_.sessionId }).Count -eq 0
    )
    Assert-Eq 'session creation sends no request body' '0,0' (
        ($sessionCreates.bodyLength) -join ','
    )

    $sessionDeletes = @($log | Where-Object operationId -CEQ 'Cis.Session_delete')
    Assert-Eq 'the run logs out exactly once' 1 $sessionDeletes.Count
    Assert-Eq 'logout succeeds' 204 $sessionDeletes[0].responseStatus
    Assert-Eq 'logout uses DELETE' 'DELETE' $sessionDeletes[0].method
    Assert-Eq 'logout retires the refreshed token' $secondToken `
        $sessionDeletes[0].sessionId

    $authenticated = @($log | Where-Object operationId -CNE 'Cis.Session_create')
    Assert-True 'every authenticated request presents a session token' (
        @($authenticated | Where-Object {
            [string]::IsNullOrEmpty($_.sessionId)
        }).Count -eq 0
    )
    Assert-True 'no request invents a token the fixture never issued' (
        @($authenticated | Where-Object {
            $_.sessionId -cnotin @($firstToken, $secondToken)
        }).Count -eq 0
    )

    # -- the expiry is observed once and the failed call is replayed -------

    $rejected = @($log | Where-Object responseStatus -EQ 401)
    Assert-Eq 'the expired token is rejected exactly once' 1 $rejected.Count
    if ($rejected.Count -eq 1) {
        $expired = $rejected[0]
        Assert-Eq 'the rejected request used the first token' $firstToken `
            $expired.sessionId
        Assert-True 'the rejection happens partway through the run' (
            $expired.sequence -gt 1 -and $expired.sequence -lt $log.Count
        )
        $refresh = @(
            $sessionCreates | Where-Object { $_.sequence -gt $expired.sequence }
        )
        Assert-Eq 'the refresh follows the rejection' 1 $refresh.Count
        $replay = @(
            $log | Where-Object {
                $_.sequence -gt $refresh[0].sequence -and
                $_.operationId -ceq $expired.operationId -and
                $_.rawTarget -ceq $expired.rawTarget -and
                $_.body -ceq $expired.body
            }
        )
        Assert-Eq 'the rejected request is replayed verbatim after the refresh' 1 `
            $replay.Count
        if ($replay.Count -ge 1) {
            Assert-Eq 'the replay succeeds' 200 $replay[0].responseStatus
            Assert-Eq 'the replay carries the refreshed token' $secondToken `
                $replay[0].sessionId
        }
        Assert-True 'the spent token is abandoned once it is rejected' (
            @($log | Where-Object {
                $_.sequence -gt $expired.sequence -and
                $_.sessionId -ceq $firstToken
            }).Count -eq 0
        )
    }

    # -- inventory lookup --------------------------------------------------

    $vmLists = @($log | Where-Object operationId -CEQ 'Vcenter.VM_list')
    Assert-Eq 'one filtered virtual machine listing' 1 $vmLists.Count
    Assert-Eq 'virtual machine listing uses GET' 'GET' $vmLists[0].method
    Assert-Eq 'virtual machine listing targets the contract path' `
        '/api/vcenter/vm' $vmLists[0].path
    Assert-Eq 'virtual machine listing succeeds' 200 $vmLists[0].responseStatus
    Assert-Eq 'virtual machine listing sends no request body' 0 `
        $vmLists[0].bodyLength
    Assert-Eq 'virtual machine listing filters on names only' 'names' (
        (Get-JsonPropertyNames $vmLists[0].query) -join ','
    )
    Assert-Eq 'virtual machine listing repeats one names value per plan VM' `
        ($planVmNames -join ',') (
        ((@($vmLists[0].query.names) | Sort-Object)) -join ','
    )
    foreach ($omitted in @(
        'vms', 'folders', 'datacenters', 'hosts',
        'clusters', 'resource_pools', 'power_states'
    )) {
        Assert-True "virtual machine listing omits unset filter $omitted" (
            (Get-JsonPropertyNames $vmLists[0].query) -cnotcontains $omitted
        )
    }

    # -- category and tag resolution ---------------------------------------

    $categoryLists = @($log | Where-Object operationId -CEQ 'Cis.Tagging.Category_list')
    Assert-Eq 'one category listing' 1 $categoryLists.Count
    Assert-Eq 'category listing sends no query string' '' $categoryLists[0].rawQuery

    $categoryGets = @($log | Where-Object operationId -CEQ 'Cis.Tagging.Category_get')
    Assert-Eq 'each listed category is resolved by name once' 3 $categoryGets.Count
    Assert-Eq 'category reads cover exactly the listed identifiers' `
        (($listedCategoryIds | Sort-Object) -join '|') (
        ((@($categoryGets.pathParameters.categoryId) | Sort-Object)) -join '|'
    )
    Assert-True 'every category read uses GET and succeeds' (
        @($categoryGets | Where-Object {
            $_.method -cne 'GET' -or $_.responseStatus -ne 200
        }).Count -eq 0
    )

    $tagListings = @(
        $log | Where-Object operationId -CEQ 'Cis.Tagging.Tag_listTagsForCategory'
    )
    Assert-Eq 'one listing of the tags in the plan category' 1 $tagListings.Count
    Assert-Eq 'tag listing carries the specified action' `
        'list-tags-for-category' $tagListings[0].query.action[0]
    Assert-Eq 'tag listing targets the contract path' `
        '/api/cis/tagging/tag' $tagListings[0].path
    $tagListingBody = $tagListings[0].body | ConvertFrom-Json
    Assert-Eq 'tag listing body carries only the required category_id' `
        'category_id' ((Get-JsonPropertyNames $tagListingBody) -join ',')
    Assert-Eq 'tag listing asks for the plan category' $categoryId `
        $tagListingBody.category_id

    $tagGets = @($log | Where-Object operationId -CEQ 'Cis.Tagging.Tag_get')
    Assert-Eq 'each tag already in the category is read once' 2 $tagGets.Count
    Assert-Eq 'tag reads cover exactly the tags the category already holds' `
        ((@($goldTagId, $legacyTagId) | Sort-Object) -join '|') (
        ((@($tagGets.pathParameters.tagId) | Sort-Object)) -join '|'
    )

    # -- tag creation omits the unset optional identifier ------------------

    $tagCreates = @($log | Where-Object operationId -CEQ 'Cis.Tagging.Tag_create')
    Assert-Eq 'only the two missing tags are created' 2 $tagCreates.Count
    Assert-True 'every tag creation succeeds' (
        @($tagCreates | Where-Object { $_.responseStatus -ne 201 }).Count -eq 0
    )
    Assert-True 'tag creation sends no action query' (
        @($tagCreates | Where-Object { $_.rawQuery -ne '' }).Count -eq 0
    )
    Assert-True 'tag creation content type is JSON' (
        @($tagCreates | Where-Object {
            $_.contentType -notlike 'application/json*'
        }).Count -eq 0
    )
    $createdTagNames = [System.Collections.Generic.List[string]]::new()
    foreach ($create in $tagCreates) {
        $body = $create.body | ConvertFrom-Json
        Assert-Eq 'tag creation body has exactly the bound members' `
            'category_id,description,name' (
            (Get-JsonPropertyNames $body) -join ','
        )
        Assert-True 'tag creation omits the unset optional tag_id' (
            $body.PSObject.Properties.Name -cnotcontains 'tag_id'
        )
        Assert-Eq 'tag creation targets the plan category' $categoryId `
            $body.category_id
        Assert-True 'tag creation carries a description' (
            -not [string]::IsNullOrWhiteSpace($body.description)
        )
        $createdTagNames.Add([string] $body.name)
    }
    Assert-Eq 'the created tags are the ones the category lacked' `
        'tier-bronze,tier-silver' (
        ((@($createdTagNames) | Sort-Object)) -join ','
    )

    # -- attachment covers the plan exactly once ---------------------------

    $attaches = @(
        $log | Where-Object {
            $_.operationId -ceq
                'Cis.Tagging.TagAssociation_attachTagToMultipleObjects' -and
            $_.responseStatus -eq 200
        }
    )
    Assert-Eq 'each assignment is attached exactly once' 3 $attaches.Count
    Assert-True 'every attachment carries the specified action' (
        @($attaches | Where-Object {
            $_.query.action[0] -cne 'attach-tag-to-multiple-objects'
        }).Count -eq 0
    )
    Assert-True 'every attachment content type is JSON' (
        @($attaches | Where-Object {
            $_.contentType -notlike 'application/json*'
        }).Count -eq 0
    )
    $expectedAttachments = @{
        $goldTagId   = 'vm-101,vm-102'
        $silverTagId = 'vm-201,vm-202,vm-203'
        $bronzeTagId = 'vm-301,vm-302'
    }
    $observedAttachments = @{}
    foreach ($attach in $attaches) {
        $body = $attach.body | ConvertFrom-Json
        Assert-Eq 'attachment body carries only object_ids' 'object_ids' (
            (Get-JsonPropertyNames $body) -join ','
        )
        $objectIds = @($body.object_ids)
        Assert-True 'attachment sends a non-empty object list' (
            $objectIds.Count -gt 0
        )
        foreach ($objectId in $objectIds) {
            Assert-Eq 'each object identifier is a Vapi.Std.DynamicID' 'id,type' (
                (Get-JsonPropertyNames $objectId) -join ','
            )
            Assert-Eq 'each object identifier names the VirtualMachine type' `
                'VirtualMachine' $objectId.type
        }
        $tagId = [string] $attach.pathParameters.tagId
        Assert-True "attachment targets a known tag ($tagId)" (
            $expectedAttachments.ContainsKey($tagId)
        )
        Assert-True "tag $tagId is attached only once" (
            -not $observedAttachments.ContainsKey($tagId)
        )
        $observedAttachments[$tagId] = (
            (@($objectIds | ForEach-Object { $_.id }) | Sort-Object) -join ','
        )
    }
    foreach ($tagId in $expectedAttachments.Keys) {
        Assert-Eq "attachment for $tagId carries the planned machines" `
            $expectedAttachments[$tagId] $observedAttachments[$tagId]
    }

    # -- the expiry is the only thing the fixture ever refuses -------------

    $refused = @(
        $log | Where-Object {
            $_.responseStatus -ge 400 -and $_.responseStatus -ne 401
        }
    )
    Assert-Eq 'the session expiry is the only refusal in the run' 0 $refused.Count
    foreach ($refusal in $refused) {
        Write-Output (
            "  refused: $($refusal.method) $($refusal.rawTarget) " +
            "-> $($refusal.responseStatus)"
        )
    }

    # -- explicit failure behavior -----------------------------------------

    # These plans exercise requirements that cannot be established from the
    # successful run alone. They remain inside the same loopback service and
    # use only contract-defined responses.
    $missingVmPlan = Join-Path $scratch 'tagplan-missing-vm.json'
    [ordered] @{
        category = [ordered] @{
            name = 'workload-tier'
            cardinality = 'MULTIPLE'
            associableTypes = @('VirtualMachine')
        }
        assignments = @(
            [ordered] @{
                tag = 'tier-gold'
                description = 'Gold tier workloads'
                vms = @('not-in-vcenter')
            }
        )
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $missingVmPlan
    Assert-Throws 'the run fails when a planned virtual machine is absent' {
        Invoke-VcfTagSync `
            -Server "127.0.0.1:$port" `
            -Credential $credential `
            -PlanPath $missingVmPlan `
            -SkipCertificateCheck
    }
    $afterMissingVm = Get-RequestLog $requestLog
    $missingVmRun = @($afterMissingVm | Where-Object {
        $_.sequence -gt $log[-1].sequence
    })
    Assert-Eq 'missing-VM failure performs one filtered inventory lookup' 1 @(
        $missingVmRun | Where-Object operationId -CEQ 'Vcenter.VM_list'
    ).Count
    Assert-Eq 'missing-VM failure stops before tag or attachment work' 0 @(
        $missingVmRun | Where-Object {
            $_.operationId -like 'Cis.Tagging.*'
        }
    ).Count

    $missingCategoryPlan = Join-Path $scratch 'tagplan-missing-category.json'
    [ordered] @{
        category = [ordered] @{
            name = 'category-not-in-vcenter'
            cardinality = 'MULTIPLE'
            associableTypes = @('VirtualMachine')
        }
        assignments = @(
            [ordered] @{
                tag = 'tier-gold'
                description = 'Gold tier workloads'
                vms = @('web-01')
            }
        )
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $missingCategoryPlan
    Assert-Throws 'the run fails rather than creating a missing category' {
        Invoke-VcfTagSync `
            -Server "127.0.0.1:$port" `
            -Credential $credential `
            -PlanPath $missingCategoryPlan `
            -SkipCertificateCheck
    }
    $afterMissingCategory = Get-RequestLog $requestLog
    $missingCategoryRun = @($afterMissingCategory | Where-Object {
        $_.sequence -gt $afterMissingVm[-1].sequence
    })
    Assert-Eq 'missing-category failure never calls tag creation' 0 @(
        $missingCategoryRun | Where-Object operationId -CEQ 'Cis.Tagging.Tag_create'
    ).Count
    Assert-Eq 'missing-category failure reads every listed category' 3 @(
        $missingCategoryRun | Where-Object operationId -CEQ 'Cis.Tagging.Category_get'
    ).Count

    $failedBatchPlan = Join-Path $scratch 'tagplan-failed-batch.json'
    [ordered] @{
        category = [ordered] @{
            name = 'workload-tier'
            cardinality = 'MULTIPLE'
            associableTypes = @('VirtualMachine')
        }
        assignments = @(
            [ordered] @{
                tag = 'tier-gold'
                description = 'Gold tier workloads'
                # The mock returns a contract-valid unsuccessful BatchResult
                # for this inventory object.
                vms = @('legacy-01')
            }
        )
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $failedBatchPlan
    Assert-Throws 'the run treats an unsuccessful attachment batch as an error' {
        Invoke-VcfTagSync `
            -Server "127.0.0.1:$port" `
            -Credential $credential `
            -PlanPath $failedBatchPlan `
            -SkipCertificateCheck
    }
    $afterFailedBatch = Get-RequestLog $requestLog
    $failedBatchRun = @($afterFailedBatch | Where-Object {
        $_.sequence -gt $afterMissingCategory[-1].sequence
    })
    $failedBatchAttaches = @($failedBatchRun | Where-Object operationId -CEQ (
        'Cis.Tagging.TagAssociation_attachTagToMultipleObjects'
    ))
    Assert-Eq 'failed-batch test reaches one attachment request' 1 `
        $failedBatchAttaches.Count
    Assert-Eq 'failed-batch response is a transport-level success' 200 `
        $failedBatchAttaches[0].responseStatus
    Assert-Eq 'failure scenarios authenticate once per invocation' 5 `
        (@($afterFailedBatch | Where-Object operationId -CEQ 'Cis.Session_create')).Count
    Assert-Eq 'failure scenarios stay inside the pinned contract' 0 @(
        $afterFailedBatch | Where-Object {
            $_.sequence -gt $log[-1].sequence -and $null -eq $_.operationId
        }
    ).Count
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
