[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fail {
    param([Parameter(Mandatory)][string]$Message)
    throw "VERIFICATION FAILED: $Message"
}

function Assert-True {
    param(
        [Parameter(Mandatory)][bool]$Condition,
        [Parameter(Mandatory)][string]$Message
    )
    if (-not $Condition) {
        Fail $Message
    }
}

function Assert-Equal {
    param(
        $Actual,
        $Expected,
        [Parameter(Mandatory)][string]$Message
    )
    if ($Actual -ne $Expected) {
        Fail "$Message (expected '$Expected', got '$Actual')"
    }
}

function Assert-Near {
    param(
        [double]$Actual,
        [double]$Expected,
        [Parameter(Mandatory)][string]$Message,
        [double]$Tolerance = 0.001
    )
    if ([math]::Abs($Actual - $Expected) -gt $Tolerance) {
        Fail "$Message (expected '$Expected', got '$Actual')"
    }
}

function Assert-SetEqual {
    param(
        [AllowEmptyCollection()][object[]]$Actual,
        [AllowEmptyCollection()][object[]]$Expected,
        [Parameter(Mandatory)][string]$Message
    )
    $actualValues = @($Actual | ForEach-Object { [string]$_ } | Sort-Object -Unique)
    $expectedValues = @($Expected | ForEach-Object { [string]$_ } | Sort-Object -Unique)
    if (($actualValues -join "`n") -cne ($expectedValues -join "`n")) {
        Fail "$Message (expected [$($expectedValues -join ', ')], got [$($actualValues -join ', ')])"
    }
}

function Assert-FileHash {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Sha256
    )
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-Equal $actual $Sha256 "Protected input changed: $Path"
}

function ConvertTo-CanonicalObject {
    param($InputObject)

    if ($null -eq $InputObject) {
        return $null
    }
    if ($InputObject -is [System.Collections.IDictionary]) {
        $ordered = [ordered]@{}
        foreach ($key in @($InputObject.Keys | Sort-Object)) {
            $ordered[[string]$key] = ConvertTo-CanonicalObject $InputObject[$key]
        }
        return $ordered
    }
    if ($InputObject -is [pscustomobject]) {
        $ordered = [ordered]@{}
        foreach ($property in @($InputObject.PSObject.Properties | Sort-Object Name)) {
            $ordered[$property.Name] = ConvertTo-CanonicalObject $property.Value
        }
        return $ordered
    }
    if (($InputObject -is [System.Collections.IEnumerable]) -and -not ($InputObject -is [string])) {
        return @($InputObject | ForEach-Object { ConvertTo-CanonicalObject $_ })
    }
    return $InputObject
}

function ConvertTo-CanonicalJson {
    param([Parameter(Mandatory)][string]$Path)
    $value = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -AsHashtable
    return (ConvertTo-CanonicalObject $value | ConvertTo-Json -Depth 100 -Compress)
}

function Get-PasswordValues {
    param(
        $Node,
        [string]$PropertyName = ''
    )

    if ($null -eq $Node) {
        return
    }
    if ($Node -is [System.Collections.IDictionary]) {
        foreach ($key in $Node.Keys) {
            Get-PasswordValues -Node $Node[$key] -PropertyName ([string]$key)
        }
        return
    }
    if ($Node -is [pscustomobject]) {
        foreach ($property in $Node.PSObject.Properties) {
            Get-PasswordValues -Node $property.Value -PropertyName $property.Name
        }
        return
    }
    if (($Node -is [System.Collections.IEnumerable]) -and -not ($Node -is [string])) {
        foreach ($item in $Node) {
            Get-PasswordValues -Node $item -PropertyName $PropertyName
        }
        return
    }
    if ($PropertyName -match 'password') {
        [string]$Node
    }
}

$workspace = Split-Path -Parent $PSScriptRoot
$sddcPath = Join-Path $workspace 'architecture/sddc-spec.json'
$openApiPath = Join-Path $workspace 'specifications/vcf-installer/vcf-installer-openapi.json'

# This is intentionally the first verification: validate the submitted artifact
# as the SddcSpec defined by the pinned VCF Installer OpenAPI document.
if (-not (Test-Path -LiteralPath $sddcPath -PathType Leaf)) {
    Fail 'architecture/sddc-spec.json is missing'
}
$openApi = Get-Content -LiteralPath $openApiPath -Raw | ConvertFrom-Json -AsHashtable
$installerSchema = [ordered]@{
    '$schema'  = 'http://json-schema.org/draft-07/schema#'
    components = $openApi.components
    '$ref'     = '#/components/schemas/SddcSpec'
}
$installerSchemaJson = $installerSchema | ConvertTo-Json -Depth 100
$sddcJson = Get-Content -LiteralPath $sddcPath -Raw
$installerSchemaErrors = @()
$installerSchemaValid = $sddcJson | Test-Json -Schema $installerSchemaJson -ErrorAction SilentlyContinue -ErrorVariable +installerSchemaErrors
if (-not $installerSchemaValid) {
    $details = @($installerSchemaErrors | ForEach-Object { $_.Exception.Message }) -join '; '
    Fail "architecture/sddc-spec.json is not a VCF Installer SddcSpec: $details"
}

# Only after the installer schema succeeds may the remaining protected checks run.
$estatePath = Join-Path $workspace 'fixtures/estate-inventory.json'
$requirementsPath = Join-Path $workspace 'fixtures/design-requirements.json'
$snapshotPath = Join-Path $workspace 'authority/compatibility-snapshot.json'
$migrationSchemaPath = Join-Path $workspace 'schemas/migration-plan.schema.json'
$migrationPath = Join-Path $workspace 'architecture/migration-plan.json'
$researchPath = Join-Path $workspace 'research/consulted-sources.json'

Assert-FileHash $openApiPath '29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d'
Assert-FileHash $estatePath '3f65b413f14b36d34a73542fbb3b78f2125087b0f914d5064708002f964fc594'
Assert-FileHash $requirementsPath '95eccac4aa6278c215d82629160f8a7dae8f01a6cf251ca8d40f61a3bf4b7f89'
Assert-FileHash $snapshotPath 'a019c9756b7dbe0b714d9c5ce2e028bec66a69b987ff1322ce7bcc0011957df7'
Assert-FileHash $migrationSchemaPath '54dfbc9eb6d633aaa6c64fa83079386195cfe468e5e98221d7f8d42fb3bccadb'

if (-not (Test-Path -LiteralPath $migrationPath -PathType Leaf)) {
    Fail 'architecture/migration-plan.json is missing'
}
$migrationSchemaJson = Get-Content -LiteralPath $migrationSchemaPath -Raw
$migrationJson = Get-Content -LiteralPath $migrationPath -Raw
$migrationSchemaErrors = @()
$migrationSchemaValid = $migrationJson | Test-Json -Schema $migrationSchemaJson -ErrorAction SilentlyContinue -ErrorVariable +migrationSchemaErrors
if (-not $migrationSchemaValid) {
    $details = @($migrationSchemaErrors | ForEach-Object { $_.Exception.Message }) -join '; '
    Fail "architecture/migration-plan.json does not conform to the fixed migration schema: $details"
}

$sddc = $sddcJson | ConvertFrom-Json
$estate = Get-Content -LiteralPath $estatePath -Raw | ConvertFrom-Json
$requirements = Get-Content -LiteralPath $requirementsPath -Raw | ConvertFrom-Json
$snapshot = Get-Content -LiteralPath $snapshotPath -Raw | ConvertFrom-Json
$migration = $migrationJson | ConvertFrom-Json

if (-not (Test-Path -LiteralPath $researchPath -PathType Leaf)) {
    Fail 'research/consulted-sources.json is missing'
}
try {
    $research = @(Get-Content -LiteralPath $researchPath -Raw | ConvertFrom-Json -DateKind String)
}
catch {
    Fail "research/consulted-sources.json is not valid JSON: $($_.Exception.Message)"
}
Assert-True ($research.Count -ge 2) 'Research must record multiple Broadcom-published sources'
$researchUrls = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($source in $research) {
    Assert-True (-not [string]::IsNullOrWhiteSpace([string]$source.title)) 'Every research source needs a title'
    Assert-True (-not [string]::IsNullOrWhiteSpace([string]$source.url)) 'Every research source needs a URL'
    $sourceUri = $null
    Assert-True ([uri]::TryCreate([string]$source.url, [System.UriKind]::Absolute, [ref]$sourceUri)) "Research URL is not absolute: $($source.url)"
    Assert-Equal $sourceUri.Scheme 'https' "Research URL must use HTTPS: $($source.url)"
    $isBroadcomHost = $sourceUri.Host.Equals('broadcom.com', [System.StringComparison]::OrdinalIgnoreCase) -or
        $sourceUri.Host.EndsWith('.broadcom.com', [System.StringComparison]::OrdinalIgnoreCase)
    Assert-True $isBroadcomHost "Research source is not Broadcom-published: $($source.url)"
    Assert-True ($researchUrls.Add($sourceUri.AbsoluteUri)) "Duplicate research URL: $($source.url)"

    $accessText = ([string]$source.accessedAtUtc).Trim()
    $accessedAt = [datetimeoffset]::MinValue
    $parsedAccessTime = [datetimeoffset]::TryParse(
        $accessText,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::RoundtripKind,
        [ref]$accessedAt
    )
    Assert-True $parsedAccessTime "Research access time is not an ISO-8601 timestamp: $($source.accessedAtUtc)"
    Assert-True ($accessText -match '(?:Z|[+]00:00)$') "Research access time is not UTC: $($source.accessedAtUtc)"

    $findingValues = @(if ($source.findings -is [string]) {
        [string]$source.findings
    }
    else {
        $source.findings | ForEach-Object { [string]$_ }
    })
    Assert-True ($findingValues.Count -gt 0) "Research source '$($source.title)' has no findings"
    foreach ($finding in $findingValues) {
        Assert-True (-not [string]::IsNullOrWhiteSpace($finding)) "Research source '$($source.title)' has an empty finding"
    }
    $sourceText = (([string]$source.title) + ' ' + ($findingValues -join ' '))
    Assert-True ($sourceText -notmatch '(?i)compatibility-snapshot|pinned snapshot|local snapshot') "The local compatibility snapshot cannot be listed as research: $($source.title)"
}

Assert-Equal $sddc.sddcId $requirements.managementDomain.sddcId 'Wrong SDDC identifier'
Assert-Equal $sddc.vcfInstanceName $requirements.managementDomain.vcfInstanceName 'Wrong VCF instance name'
Assert-Equal $sddc.workflowType 'VCF' 'Greenfield workflow must be VCF'
Assert-Equal $sddc.version $requirements.targetBundleVersion 'Wrong SddcSpec target version'
Assert-True ($sddc.ceipEnabled -eq $requirements.managementDomain.ceipEnabled) 'CEIP setting does not match the requirement'
Assert-True ($sddc.skipEsxThumbprintValidation -eq $false) 'ESXi thumbprint validation must not be skipped'
Assert-True ($sddc.skipGatewayPingValidation -eq $false) 'Gateway validation must not be skipped'

$actualHosts = @($sddc.hostSpecs)
Assert-Equal $actualHosts.Count $requirements.managementDomain.hostCount 'Wrong management-domain host count'
Assert-SetEqual @($actualHosts.hostname) @($requirements.managementDomain.hostnames) 'Management-domain hostnames differ'
foreach ($hostSpec in $actualHosts) {
    Assert-Equal $hostSpec.credentials.username 'root' "Wrong ESXi user for $($hostSpec.hostname)"
    Assert-Equal $hostSpec.credentials.password $requirements.managementDomain.passwordPlaceholders.esxiRoot "Wrong ESXi placeholder for $($hostSpec.hostname)"
}

Assert-Equal $sddc.dnsSpec.subdomain $requirements.targetSite.dnsSubdomain 'Wrong DNS subdomain'
Assert-SetEqual @($sddc.dnsSpec.nameservers) @($requirements.targetSite.dnsServers) 'Wrong DNS servers'
Assert-SetEqual @($sddc.ntpServers) @($requirements.targetSite.ntpServers) 'Wrong NTP servers'

Assert-Equal $sddc.vcenterSpec.vcenterHostname $requirements.managementDomain.vcenterHostname 'Wrong vCenter hostname'
Assert-Equal $sddc.vcenterSpec.rootVcenterPassword $requirements.managementDomain.passwordPlaceholders.vcenterRoot 'Wrong vCenter root placeholder'
Assert-Equal $sddc.vcenterSpec.adminUserSsoPassword $requirements.managementDomain.passwordPlaceholders.vcenterSso 'Wrong vCenter SSO placeholder'
Assert-Equal $sddc.vcenterSpec.ssoDomain $requirements.managementDomain.ssoDomain 'Wrong SSO domain'
Assert-Equal $sddc.vcenterSpec.vmSize $requirements.managementDomain.vcenterVmSize 'Wrong vCenter size'
Assert-Equal $sddc.vcenterSpec.version $requirements.targetBundleVersion 'Wrong vCenter target version'
Assert-True ($sddc.vcenterSpec.useExistingDeployment -eq $false) 'Source vCenter must not be reused'

Assert-Equal $sddc.clusterSpec.datacenterName $requirements.managementDomain.datacenterName 'Wrong datacenter name'
Assert-Equal $sddc.clusterSpec.clusterName $requirements.managementDomain.clusterName 'Wrong management cluster name'
Assert-Equal $sddc.datastoreSpec.vsanSpec.datastoreName $requirements.managementDomain.datastoreName 'Wrong vSAN datastore name'
Assert-True ($sddc.datastoreSpec.vsanSpec.esaConfig.enabled -eq $true) 'vSAN ESA must be enabled'
Assert-Equal $sddc.datastoreSpec.vsanSpec.failuresToTolerate $requirements.availability.managementHostFailuresToTolerate 'Wrong vSAN failures-to-tolerate value'

Assert-SetEqual @($sddc.networkSpecs.networkType) @($requirements.networks.networkType) 'Network types differ from the site design'
foreach ($expectedNetwork in $requirements.networks) {
    $matches = @($sddc.networkSpecs | Where-Object { $_.networkType -eq $expectedNetwork.networkType })
    Assert-Equal $matches.Count 1 "Expected exactly one $($expectedNetwork.networkType) network"
    $actualNetwork = $matches[0]
    Assert-Equal $actualNetwork.vlanId $expectedNetwork.vlanId "Wrong VLAN for $($expectedNetwork.networkType)"
    Assert-Equal $actualNetwork.subnet $expectedNetwork.subnet "Wrong subnet for $($expectedNetwork.networkType)"
    Assert-Equal $actualNetwork.gateway $expectedNetwork.gateway "Wrong gateway for $($expectedNetwork.networkType)"
    Assert-Equal $actualNetwork.mtu $expectedNetwork.mtu "Wrong MTU for $($expectedNetwork.networkType)"
    Assert-Equal $actualNetwork.portGroupKey $expectedNetwork.portGroupKey "Wrong port group for $($expectedNetwork.networkType)"
    if ($null -ne $expectedNetwork.PSObject.Properties['ipRange']) {
        Assert-Equal @($actualNetwork.includeIpAddressRanges).Count 1 "Wrong IP range count for $($expectedNetwork.networkType)"
        Assert-Equal $actualNetwork.includeIpAddressRanges[0].startIpAddress $expectedNetwork.ipRange.start "Wrong start IP for $($expectedNetwork.networkType)"
        Assert-Equal $actualNetwork.includeIpAddressRanges[0].endIpAddress $expectedNetwork.ipRange.end "Wrong end IP for $($expectedNetwork.networkType)"
    }
}

Assert-Equal @($sddc.dvsSpecs).Count 1 'Exactly one distributed switch is required'
$dvs = $sddc.dvsSpecs[0]
Assert-Equal $dvs.dvsName $requirements.distributedSwitch.name 'Wrong distributed switch name'
Assert-Equal $dvs.mtu $requirements.distributedSwitch.mtu 'Wrong distributed switch MTU'
Assert-SetEqual @($dvs.networks) @($requirements.networks.networkType) 'Wrong distributed switch network membership'
$actualNicMappings = @($dvs.vmnicsToUplinks | ForEach-Object { "$($_.id)=$($_.uplink)" })
$expectedNicMappings = @($requirements.distributedSwitch.vmnicsToUplinks | ForEach-Object { "$($_.id)=$($_.uplink)" })
Assert-SetEqual $actualNicMappings $expectedNicMappings 'Wrong vmnic-to-uplink mappings'

Assert-Equal @($sddc.nsxtSpec.nsxtManagers).Count $requirements.availability.nsxManagerNodeCount 'Wrong NSX manager count'
Assert-SetEqual @($sddc.nsxtSpec.nsxtManagers.hostname) @($requirements.managementDomain.nsxManagerHostnames) 'Wrong NSX manager hostnames'
Assert-Equal $sddc.nsxtSpec.vipFqdn $requirements.managementDomain.nsxVipFqdn 'Wrong NSX VIP'
Assert-Equal $sddc.nsxtSpec.nsxtManagerSize $requirements.managementDomain.nsxManagerSize 'Wrong NSX manager size'
Assert-True ($sddc.nsxtSpec.useExistingDeployment -eq $false) 'Source NSX must not be reused'
Assert-Equal $sddc.nsxtSpec.transportVlanId $requirements.nsxHostOverlay.transportVlanId 'Wrong NSX transport VLAN'
Assert-Equal $sddc.nsxtSpec.version $requirements.targetBundleVersion 'Wrong NSX target version'
Assert-Equal $sddc.nsxtSpec.ipAddressPoolSpec.name $requirements.nsxHostOverlay.poolName 'Wrong NSX TEP pool name'
Assert-Equal $sddc.nsxtSpec.ipAddressPoolSpec.subnets[0].cidr $requirements.nsxHostOverlay.cidr 'Wrong NSX TEP CIDR'
Assert-Equal $sddc.nsxtSpec.ipAddressPoolSpec.subnets[0].gateway $requirements.nsxHostOverlay.gateway 'Wrong NSX TEP gateway'
Assert-Equal @($sddc.nsxtSpec.ipAddressPoolSpec.subnets[0].ipAddressPoolRanges).Count 1 'Wrong NSX TEP range count'
Assert-Equal $sddc.nsxtSpec.ipAddressPoolSpec.subnets[0].ipAddressPoolRanges[0].start $requirements.nsxHostOverlay.rangeStart 'Wrong NSX TEP range start'
Assert-Equal $sddc.nsxtSpec.ipAddressPoolSpec.subnets[0].ipAddressPoolRanges[0].end $requirements.nsxHostOverlay.rangeEnd 'Wrong NSX TEP range end'

Assert-Equal $sddc.sddcManagerSpec.hostname $requirements.managementDomain.sddcManagerHostname 'Wrong SDDC Manager hostname'
Assert-Equal $sddc.sddcManagerSpec.version $requirements.targetBundleVersion 'Wrong SDDC Manager target version'
Assert-True ($sddc.sddcManagerSpec.useExistingDeployment -eq $requirements.managementDomain.installerBecomesSddcManager) 'VCF Installer-to-SDDC Manager disposition is wrong'
Assert-Equal @($sddc.vcfOperationsSpec.nodes).Count $requirements.availability.operationsNodeCount 'Wrong Operations node count'
Assert-SetEqual @($sddc.vcfOperationsSpec.nodes.hostname) @($requirements.managementDomain.operationsHostnames) 'Wrong Operations hostnames'
Assert-SetEqual @($sddc.vcfOperationsSpec.nodes.type) @('master', 'replica', 'data') 'Operations must use master, replica, and data nodes'
Assert-Equal $sddc.vcfOperationsSpec.loadBalancerFqdn $requirements.managementDomain.operationsLoadBalancerFqdn 'Wrong Operations load balancer FQDN'
Assert-Equal $sddc.vcfOperationsSpec.version $requirements.targetBundleVersion 'Wrong Operations target version'
Assert-True ($sddc.vcfOperationsSpec.useExistingDeployment -eq $false) 'Source Operations deployment must not be reused'
Assert-Equal $sddc.vspClusterSpec.platformFqdn $requirements.managementDomain.vspPlatformFqdn 'Wrong VSP platform FQDN'
Assert-Equal $sddc.vspClusterSpec.instanceFqdn $requirements.managementDomain.vspInstanceFqdn 'Wrong VSP instance FQDN'
Assert-Equal $sddc.vspClusterSpec.fleetFqdn $requirements.managementDomain.vspFleetFqdn 'Wrong VSP fleet FQDN'
Assert-Equal $sddc.vspClusterSpec.ipv4Pool.ipRange.startIpAddress $requirements.managementDomain.managementServicesIpRange.start 'Wrong management-services pool start'
Assert-Equal $sddc.vspClusterSpec.ipv4Pool.ipRange.endIpAddress $requirements.managementDomain.managementServicesIpRange.end 'Wrong management-services pool end'
Assert-Equal $sddc.vspClusterSpec.internalClusterCidrIpv4 $requirements.managementDomain.internalServicesCidr 'Wrong internal services CIDR'
Assert-Equal $sddc.vspClusterSpec.version $requirements.targetBundleVersion 'Wrong VSP target version'
Assert-True ($sddc.vspClusterSpec.useExistingDeployment -eq $false) 'Source VSP deployment must not be reused'
Assert-Equal $sddc.vidbSpec.hostname $requirements.managementDomain.vidbHostname 'Wrong identity broker hostname'
Assert-Equal $sddc.licenseServerSpec.hostname $requirements.managementDomain.licenseServerHostname 'Wrong license server hostname'
Assert-True ($sddc.licenseServerSpec.useExistingDeployment -eq $false) 'A new target license server is required'

foreach ($serviceProperty in @('fleetLcmSpec', 'sddcLcmSpec', 'fleetDepotSpec', 'telemetryAcceptorSpec', 'vidbSpec', 'licenseServerSpec')) {
    Assert-Equal $sddc.$serviceProperty.version $requirements.targetBundleVersion "Wrong target version for $serviceProperty"
}

$allowedPlaceholders = @($requirements.managementDomain.passwordPlaceholders.PSObject.Properties.Value)
foreach ($passwordValue in @(Get-PasswordValues -Node $sddc)) {
    Assert-True ($allowedPlaceholders -ccontains $passwordValue) "SddcSpec contains an unapproved password value '$passwordValue'"
}
foreach ($sourceComponent in $estate.components) {
    Assert-True (-not $sddcJson.Contains([string]$sourceComponent.build)) "SddcSpec reuses source build $($sourceComponent.build)"
    Assert-True (-not $sddcJson.Contains([string]$sourceComponent.componentId)) "SddcSpec reuses source component $($sourceComponent.componentId)"
    foreach ($sourceInstance in @($sourceComponent.instances)) {
        Assert-True (-not $sddcJson.Contains([string]$sourceInstance)) "SddcSpec reuses source instance $sourceInstance"
    }
}

Assert-Equal $migration.inventoryId $estate.inventoryId 'Migration plan inventory ID is wrong'
Assert-Equal $migration.targetDesign.sourceSiteId $requirements.sourceSiteId 'Wrong source site'
Assert-Equal $migration.targetDesign.targetSiteId $requirements.targetSite.siteId 'Wrong target site'
Assert-Equal $migration.targetDesign.targetBundleVersion $requirements.targetBundleVersion 'Wrong migration target bundle'
Assert-Equal $migration.targetDesign.strategy $snapshot.migrationConstraints.strategy 'Wrong migration strategy'
Assert-True ($migration.targetDesign.layer2Stretch -eq $requirements.targetSite.layer2StretchAllowed) 'Layer-2 stretch decision violates the site requirement'
Assert-True ($migration.targetDesign.retainSourceUntilTargetValidation -eq $requirements.targetSite.retainSourceUntilTargetValidation) 'Source retention decision violates the site requirement'

foreach ($property in $requirements.availability.PSObject.Properties) {
    Assert-Equal $migration.targetDesign.availability.($property.Name) $property.Value "Wrong availability value: $($property.Name)"
}

$demand = $estate.workloadDemand
$profile = $requirements.workloadDomain.hostProfile
$headroom = [double]$requirements.workloadDomain.headroomMultiplier
$failures = [int]$requirements.availability.workloadHostFailuresToTolerate
$requiredCpu = ([double]$demand.allocatedVcpu / [double]$requirements.workloadDomain.cpuOversubscriptionRatio) * $headroom
$requiredMemory = [double]$demand.allocatedMemoryTiB * $headroom
$requiredStorage = [double]$demand.usedStorageTiB * $headroom
$cpuHosts = [math]::Ceiling($requiredCpu / [double]$profile.physicalCores) + $failures
$memoryHosts = [math]::Ceiling($requiredMemory / [double]$profile.memoryTiB) + $failures
$usablePerHost = [double]$profile.rawVsanTiB * [double]$profile.vsanUsableFraction
$storageHosts = [math]::Ceiling($requiredStorage / $usablePerHost) + $failures
$minimumHosts = [int][math]::Max($cpuHosts, [math]::Max($memoryHosts, $storageHosts))
$expectedLimiting = @()
if ($cpuHosts -eq $minimumHosts) { $expectedLimiting += 'cpu' }
if ($memoryHosts -eq $minimumHosts) { $expectedLimiting += 'memory' }
if ($storageHosts -eq $minimumHosts) { $expectedLimiting += 'storage' }

$capacity = $migration.targetDesign.capacity
Assert-Equal $capacity.managementDomainHostCount $requirements.managementDomain.hostCount 'Wrong management-domain capacity count'
Assert-Equal $capacity.minimumWorkloadHostCount $minimumHosts 'Wrong minimum workload host count'
Assert-True ($capacity.selectedWorkloadHostCount -ge $minimumHosts) 'Selected workload host count is below the derived minimum'
Assert-SetEqual @($capacity.limitingResources) $expectedLimiting 'Wrong capacity limiting resources'
Assert-Near $capacity.requiredWithHeadroom.physicalCores $requiredCpu 'Wrong required CPU with headroom'
Assert-Near $capacity.requiredWithHeadroom.memoryTiB $requiredMemory 'Wrong required memory with headroom'
Assert-Near $capacity.requiredWithHeadroom.usableVsanTiB $requiredStorage 'Wrong required storage with headroom'

$survivingHosts = [int]$capacity.selectedWorkloadHostCount - $failures
$expectedPostCpu = $survivingHosts * [double]$profile.physicalCores
$expectedPostMemory = $survivingHosts * [double]$profile.memoryTiB
$expectedPostStorage = $survivingHosts * $usablePerHost
Assert-Near $capacity.postFailureCapacity.physicalCores $expectedPostCpu 'Wrong post-failure CPU capacity'
Assert-Near $capacity.postFailureCapacity.memoryTiB $expectedPostMemory 'Wrong post-failure memory capacity'
Assert-Near $capacity.postFailureCapacity.usableVsanTiB $expectedPostStorage 'Wrong post-failure storage capacity'
Assert-True ($capacity.postFailureCapacity.physicalCores -ge $requiredCpu) 'Post-failure CPU capacity is insufficient'
Assert-True ($capacity.postFailureCapacity.memoryTiB -ge $requiredMemory) 'Post-failure memory capacity is insufficient'
Assert-True ($capacity.postFailureCapacity.usableVsanTiB -ge $requiredStorage) 'Post-failure storage capacity is insufficient'

$steps = @($migration.steps)
Assert-Equal $steps.Count @($estate.components).Count 'Migration plan must contain one step per inventoried component'
Assert-Equal @($steps.componentId | Sort-Object -Unique).Count $steps.Count 'Migration component IDs must be unique'
Assert-Equal @($steps.order | Sort-Object -Unique).Count $steps.Count 'Migration step orders must be unique'
Assert-SetEqual @($steps.order) @(1..$steps.Count) 'Migration step order must be consecutive'

foreach ($component in $estate.components) {
    $componentSteps = @($steps | Where-Object { $_.componentId -eq $component.componentId })
    Assert-Equal $componentSteps.Count 1 "Missing or duplicate plan step for $($component.componentId)"
    $step = $componentSteps[0]
    Assert-Equal $step.componentType $component.componentType "Wrong component type for $($component.componentId)"
    Assert-Equal $step.productName $component.productName "Wrong product name for $($component.componentId)"
    Assert-Equal $step.sourceVersion $component.version "Wrong source version for $($component.componentId)"
    Assert-Equal $step.sourceBuild $component.build "Wrong source build for $($component.componentId)"

    $targetMatches = @($snapshot.targetBundle.components | Where-Object { $_.componentType -eq $component.componentType })
    Assert-Equal $targetMatches.Count 1 "Snapshot has no unique target for $($component.componentType)"
    $target = $targetMatches[0]
    Assert-Equal $step.targetVersion $target.version "Wrong target version for $($component.componentId)"
    Assert-Equal $step.targetBuild $target.build "Wrong target build for $($component.componentId)"

    $pathMatches = @($snapshot.pathRules | Where-Object {
        $_.componentType -eq $component.componentType -and
        $_.sourceVersion -eq $component.version -and
        $_.sourceBuild -eq $component.build -and
        $_.targetVersion -eq $target.version -and
        $_.targetBuild -eq $target.build
    })
    Assert-Equal $pathMatches.Count 1 "Snapshot has no unique path rule for $($component.componentId)"
    $pathRule = $pathMatches[0]
    Assert-True ($pathRule.directUpgradeSupported -eq $false) "Pinned path unexpectedly permits a direct upgrade for $($component.componentId)"
    Assert-Equal $step.disposition $pathRule.requiredDisposition "Wrong disposition for $($component.componentId)"
    Assert-Equal $step.compatibilityReason $pathRule.reasonCode "Wrong compatibility reason for $($component.componentId)"

    $migrationRuleMatches = @($snapshot.migrationConstraints.componentRules | Where-Object { $_.componentType -eq $component.componentType })
    Assert-Equal $migrationRuleMatches.Count 1 "Snapshot has no unique migration rule for $($component.componentId)"
    $migrationRule = $migrationRuleMatches[0]
    $expectedPredecessorIds = foreach ($predecessorType in @($migrationRule.predecessors)) {
        $predecessorComponents = @($estate.components | Where-Object { $_.componentType -eq $predecessorType })
        Assert-Equal $predecessorComponents.Count 1 "Inventory has no unique predecessor of type $predecessorType"
        $predecessorComponents[0].componentId
    }
    Assert-SetEqual @($step.predecessors) @($expectedPredecessorIds) "Wrong predecessors for $($component.componentId)"
    Assert-SetEqual @($step.gates) @($migrationRule.requiredTechnicalGates) "Wrong technical gates for $($component.componentId)"
    foreach ($predecessorId in @($step.predecessors)) {
        $predecessorStep = @($steps | Where-Object { $_.componentId -eq $predecessorId })[0]
        Assert-True ($predecessorStep.order -lt $step.order) "Predecessor $predecessorId must precede $($component.componentId)"
    }
}

$manifestPath = Join-Path $workspace 'VcfArchitecture/VcfArchitecture.psd1'
$modulePath = Join-Path $workspace 'VcfArchitecture/VcfArchitecture.psm1'
Assert-True (Test-Path -LiteralPath $manifestPath -PathType Leaf) 'PowerShell module manifest is missing'
Assert-True (Test-Path -LiteralPath $modulePath -PathType Leaf) 'PowerShell module implementation is missing'
$manifest = Import-PowerShellDataFile -LiteralPath $manifestPath
$requiredModuleNames = foreach ($requiredModule in @($manifest.RequiredModules)) {
    if ($requiredModule -is [string]) { $requiredModule } else { $requiredModule.ModuleName }
}
Assert-True ($requiredModuleNames -contains 'VMware.Sdk.Vcf.Installer') 'Module manifest must require VMware.Sdk.Vcf.Installer'
Assert-SetEqual @($manifest.FunctionsToExport) @('New-VcfArchitecture', 'Test-VcfArchitectureSpec') 'Wrong exported functions'

$tokens = $null
$parseErrors = $null
$moduleAst = [System.Management.Automation.Language.Parser]::ParseFile($modulePath, [ref]$tokens, [ref]$parseErrors)
Assert-Equal @($parseErrors).Count 0 'PowerShell module contains syntax errors'
$functionNames = @($moduleAst.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true).Name)
Assert-True ($functionNames -contains 'New-VcfArchitecture') 'New-VcfArchitecture is not implemented'
Assert-True ($functionNames -contains 'Test-VcfArchitectureSpec') 'Test-VcfArchitectureSpec is not implemented'
$commandNames = @($moduleAst.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true) | ForEach-Object { $_.GetCommandName() })
Assert-True ($commandNames -contains 'Initialize-VcfInstallerSddcSpec') 'Module does not construct the VMware SDK SddcSpec model'
Assert-True ($commandNames -contains 'Invoke-VcfInstallerValidateSddcSpec') 'Module does not integrate with the VCF Installer validation API'
$deploymentCommands = @($commandNames | Where-Object {
    $_ -and
    $_ -ne 'Invoke-VcfInstallerValidateSddcSpec' -and
    $_ -match '^Invoke-VcfInstaller.*(Create|Deploy|Install|Start)'
})
Assert-Equal $deploymentCommands.Count 0 'Module must not invoke VCF deployment execution'

$moduleDirectory = Split-Path -Parent $modulePath
$vendoredBinaries = @(Get-ChildItem -LiteralPath $moduleDirectory -Recurse -File | Where-Object {
    $_.Extension -in @('.dll', '.nupkg') -or $_.Name -match '^VMware\.Sdk\.'
})
Assert-Equal $vendoredBinaries.Count 0 'Do not vendor VMware SDK modules or binaries'

# The real SDK is a declared target-environment prerequisite, not a verifier
# dependency. These generic test doubles execute every SDK builder call and let
# verification prove that Test-VcfArchitectureSpec submits the builder-produced
# SddcSpec without downloading or embedding VMware code.
$initializerCommandNames = @($commandNames | Where-Object { $_ -match '^Initialize-VcfInstaller' } | Sort-Object -Unique)
$sdkStubNames = @($initializerCommandNames) + 'Invoke-VcfInstallerValidateSddcSpec'
$initializerStub = {
    $parameters = [ordered]@{}
    for ($argumentIndex = 0; $argumentIndex -lt $args.Count; $argumentIndex += 2) {
        $parameterToken = [string]$args[$argumentIndex]
        if (-not $parameterToken.StartsWith('-') -or $argumentIndex + 1 -ge $args.Count) {
            throw "Malformed initializer invocation for $($MyInvocation.MyCommand.Name)"
        }
        $parameters[$parameterToken.Substring(1)] = $args[$argumentIndex + 1]
    }
    [pscustomobject]@{
        Initializer = $MyInvocation.MyCommand.Name
        Arguments   = @($args)
        Parameters  = $parameters
    }
}
$global:VcfArchitectureValidationCalls = [System.Collections.Generic.List[object]]::new()
$validationStub = {
    $global:VcfArchitectureValidationCalls.Add([pscustomobject]@{
        Arguments = @($args)
    }) | Out-Null
    [pscustomobject]@{ Submitted = $true }
}
foreach ($commandName in $initializerCommandNames) {
    Set-Item -Path ("Function:\global:" + $commandName) -Value $initializerStub -Force
}
Set-Item -Path 'Function:\global:Invoke-VcfInstallerValidateSddcSpec' -Value $validationStub -Force

$verificationRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("vcfarch-verify-" + [guid]::NewGuid().ToString('N'))
$verificationOutput = Join-Path $verificationRoot 'original'
$moduleUnderTest = $null
try {
    $moduleUnderTest = Import-Module -Name $modulePath -Force -PassThru
    New-VcfArchitecture -DesignRequirementsPath $requirementsPath -EstateInventoryPath $estatePath -CompatibilitySnapshotPath $snapshotPath -OutputDirectory $verificationOutput | Out-Null
    $generatedSddcPath = Join-Path $verificationOutput 'sddc-spec.json'
    $generatedMigrationPath = Join-Path $verificationOutput 'migration-plan.json'
    Assert-True (Test-Path -LiteralPath $generatedSddcPath -PathType Leaf) 'New-VcfArchitecture did not generate sddc-spec.json'
    Assert-True (Test-Path -LiteralPath $generatedMigrationPath -PathType Leaf) 'New-VcfArchitecture did not generate migration-plan.json'
    Assert-Equal (ConvertTo-CanonicalJson $generatedSddcPath) (ConvertTo-CanonicalJson $sddcPath) 'Committed SddcSpec is not reproducible from the module'
    Assert-Equal (ConvertTo-CanonicalJson $generatedMigrationPath) (ConvertTo-CanonicalJson $migrationPath) 'Committed migration plan is not reproducible from the module'

    Test-VcfArchitectureSpec -Path $generatedSddcPath | Out-Null
    Assert-Equal $global:VcfArchitectureValidationCalls.Count 1 'Test-VcfArchitectureSpec did not submit exactly one validation request'
    $validationArguments = @($global:VcfArchitectureValidationCalls[0].Arguments)
    $submittedSpec = $null
    for ($argumentIndex = 0; $argumentIndex -lt ($validationArguments.Count - 1); $argumentIndex++) {
        if ($validationArguments[$argumentIndex] -ceq '-SddcSpec') {
            $submittedSpec = $validationArguments[$argumentIndex + 1]
            break
        }
    }
    Assert-True ($null -ne $submittedSpec) 'VCF Installer validation was not called with -SddcSpec'
    Assert-Equal $submittedSpec.Initializer 'Initialize-VcfInstallerSddcSpec' 'Validation did not receive the SDK-built SddcSpec'
    $sdkSpecParameters = $submittedSpec.Parameters
    Assert-Equal $sdkSpecParameters['SddcId'] $sddc.sddcId 'SDK-built SddcSpec has the wrong SDDC identifier'
    Assert-Equal $sdkSpecParameters['Version'] $sddc.version 'SDK-built SddcSpec has the wrong target version'
    Assert-Equal $sdkSpecParameters['WorkflowType'] $sddc.workflowType 'SDK-built SddcSpec has the wrong workflow type'
    Assert-Equal $sdkSpecParameters['VcenterSpec'].Initializer 'Initialize-VcfInstallerSddcVcenterSpec' 'vCenter was not converted to its SDK model'
    Assert-Equal $sdkSpecParameters['ClusterSpec'].Initializer 'Initialize-VcfInstallerSddcClusterSpec' 'Cluster was not converted to its SDK model'
    Assert-Equal $sdkSpecParameters['DnsSpec'].Initializer 'Initialize-VcfInstallerDnsSpec' 'DNS was not converted to its SDK model'
    Assert-Equal $sdkSpecParameters['NsxtSpec'].Initializer 'Initialize-VcfInstallerSddcNsxtSpec' 'NSX was not converted to its SDK model'
    Assert-Equal $sdkSpecParameters['SddcManagerSpec'].Initializer 'Initialize-VcfInstallerSddcManagerSpec' 'SDDC Manager was not converted to its SDK model'
    Assert-Equal $sdkSpecParameters['DatastoreSpec'].Initializer 'Initialize-VcfInstallerSddcDatastoreSpec' 'Datastore was not converted to its SDK model'
    Assert-Equal $sdkSpecParameters['VspClusterSpec'].Initializer 'Initialize-VcfInstallerSddcVspClusterSpec' 'VCF Management Services were not converted to the SDK model'
    Assert-Equal $sdkSpecParameters['VcfOperationsSpec'].Initializer 'Initialize-VcfInstallerVcfOperationsSpec' 'Operations was not converted to its SDK model'
    Assert-Equal $sdkSpecParameters['FleetLcmSpec'].Initializer 'Initialize-VcfInstallerFleetLcmServiceSpec' 'Fleet LCM was not converted to its SDK model'
    Assert-Equal $sdkSpecParameters['SddcLcmSpec'].Initializer 'Initialize-VcfInstallerSddcLcmServiceSpec' 'SDDC LCM was not converted to its SDK model'
    Assert-Equal $sdkSpecParameters['FleetDepotSpec'].Initializer 'Initialize-VcfInstallerFleetDepotServiceSpec' 'Fleet Depot was not converted to its SDK model'
    Assert-Equal $sdkSpecParameters['TelemetryAcceptorSpec'].Initializer 'Initialize-VcfInstallerTelemetryAcceptorSpec' 'Telemetry Acceptor was not converted to its SDK model'
    Assert-Equal $sdkSpecParameters['VidbSpec'].Initializer 'Initialize-VcfInstallerVidbSpec' 'vIDB was not converted to its SDK model'
    Assert-Equal $sdkSpecParameters['LicenseServerSpec'].Initializer 'Initialize-VcfInstallerLicenseServerSpec' 'License Server was not converted to its SDK model'

    $sdkHosts = @($sdkSpecParameters['HostSpecs'])
    Assert-Equal $sdkHosts.Count @($sddc.hostSpecs).Count 'Wrong SDK host model count'
    foreach ($sdkHost in $sdkHosts) {
        Assert-Equal $sdkHost.Initializer 'Initialize-VcfInstallerSddcHostSpec' 'A host was not converted to the SDK model'
        Assert-Equal $sdkHost.Parameters['Credentials'].Initializer 'Initialize-VcfInstallerSddcCredentials' 'Host credentials were not converted to the SDK model'
    }
    $sdkNetworks = @($sdkSpecParameters['NetworkSpecs'])
    Assert-Equal $sdkNetworks.Count @($sddc.networkSpecs).Count 'Wrong SDK network model count'
    foreach ($sdkNetwork in $sdkNetworks) {
        Assert-Equal $sdkNetwork.Initializer 'Initialize-VcfInstallerSddcNetworkSpec' 'A network was not converted to the SDK model'
        if ($sdkNetwork.Parameters.Contains('IncludeIpAddressRanges')) {
            foreach ($sdkRange in @($sdkNetwork.Parameters['IncludeIpAddressRanges'])) {
                Assert-Equal $sdkRange.Initializer 'Initialize-VcfInstallerIpRange' 'A network IP range was not converted to the SDK model'
            }
        }
    }
    foreach ($sdkDvs in @($sdkSpecParameters['DvsSpecs'])) {
        Assert-Equal $sdkDvs.Initializer 'Initialize-VcfInstallerDvsSpec' 'A distributed switch was not converted to the SDK model'
        foreach ($sdkMapping in @($sdkDvs.Parameters['VmnicsToUplinks'])) {
            Assert-Equal $sdkMapping.Initializer 'Initialize-VcfInstallerVmnicToUplink' 'A vmnic mapping was not converted to the SDK model'
        }
    }
    foreach ($sdkNsxManager in @($sdkSpecParameters['NsxtSpec'].Parameters['NsxtManagers'])) {
        Assert-Equal $sdkNsxManager.Initializer 'Initialize-VcfInstallerNsxtManagerSpec' 'An NSX manager was not converted to the SDK model'
    }
    $sdkTepPool = $sdkSpecParameters['NsxtSpec'].Parameters['IpAddressPoolSpec']
    Assert-Equal $sdkTepPool.Initializer 'Initialize-VcfInstallerIpAddressPoolSpec' 'NSX TEP pool was not converted to the SDK model'
    foreach ($sdkTepSubnet in @($sdkTepPool.Parameters['Subnets'])) {
        Assert-Equal $sdkTepSubnet.Initializer 'Initialize-VcfInstallerIpAddressPoolSubnetSpec' 'NSX TEP subnet was not converted to the SDK model'
        foreach ($sdkTepRange in @($sdkTepSubnet.Parameters['IpAddressPoolRanges'])) {
            Assert-Equal $sdkTepRange.Initializer 'Initialize-VcfInstallerIpAddressPoolRangeSpec' 'NSX TEP range was not converted to the SDK model'
        }
    }
    $sdkVsan = $sdkSpecParameters['DatastoreSpec'].Parameters['VsanSpec']
    Assert-Equal $sdkVsan.Initializer 'Initialize-VcfInstallerVsanSpec' 'vSAN was not converted to the SDK model'
    Assert-Equal $sdkVsan.Parameters['EsaConfig'].Initializer 'Initialize-VcfInstallerVsanEsaConfig' 'vSAN ESA was not converted to the SDK model'
    foreach ($sdkOperationsNode in @($sdkSpecParameters['VcfOperationsSpec'].Parameters['Nodes'])) {
        Assert-Equal $sdkOperationsNode.Initializer 'Initialize-VcfInstallerVcfOperationsNode' 'An Operations node was not converted to the SDK model'
    }
    $sdkVspPool = $sdkSpecParameters['VspClusterSpec'].Parameters['Ipv4Pool']
    Assert-Equal $sdkVspPool.Initializer 'Initialize-VcfInstallerIPv4Pool' 'Management Services IP pool was not converted to the SDK model'
    Assert-Equal $sdkVspPool.Parameters['IpRange'].Initializer 'Initialize-VcfInstallerIpRange' 'Management Services IP range was not converted to the SDK model'

    # Mutation probes ensure New-VcfArchitecture actually consumes all three
    # inputs rather than replaying the committed artifacts.
    $mutationInput = Join-Path $verificationRoot 'mutation-input'
    $mutationOutput = Join-Path $verificationRoot 'mutation-output'
    $null = New-Item -ItemType Directory -Path $mutationInput -Force
    $mutatedRequirements = Get-Content -LiteralPath $requirementsPath -Raw | ConvertFrom-Json
    $mutatedRequirements.managementDomain.sddcId = 'chi01-m02'
    $mutatedRequirementsPath = Join-Path $mutationInput 'design-requirements.json'
    $mutatedRequirements | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $mutatedRequirementsPath -Encoding utf8

    $mutatedEstate = Get-Content -LiteralPath $estatePath -Raw | ConvertFrom-Json
    $mutatedEstate.inventoryId = 'mutation-inventory'
    $mutatedEstate.workloadDemand.allocatedVcpu = 256
    $mutatedEstate.workloadDemand.allocatedMemoryTiB = 2.0
    $mutatedEstate.workloadDemand.usedStorageTiB = 20.0
    $mutatedEstatePath = Join-Path $mutationInput 'estate-inventory.json'
    $mutatedEstate | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $mutatedEstatePath -Encoding utf8

    $mutatedSnapshot = Get-Content -LiteralPath $snapshotPath -Raw | ConvertFrom-Json
    $mutatedTarget = @($mutatedSnapshot.targetBundle.components | Where-Object { $_.componentType -eq 'ESX_HOST' })[0]
    $mutatedPathRule = @($mutatedSnapshot.pathRules | Where-Object { $_.componentType -eq 'ESX_HOST' })[0]
    $mutatedTarget.build = '25379999'
    $mutatedPathRule.targetBuild = '25379999'
    $mutatedPathRule.reasonCode = 'MUTATION_PROBE'
    $mutatedSnapshotPath = Join-Path $mutationInput 'compatibility-snapshot.json'
    $mutatedSnapshot | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $mutatedSnapshotPath -Encoding utf8

    New-VcfArchitecture -DesignRequirementsPath $mutatedRequirementsPath -EstateInventoryPath $mutatedEstatePath -CompatibilitySnapshotPath $mutatedSnapshotPath -OutputDirectory $mutationOutput | Out-Null
    $mutatedSddc = Get-Content -LiteralPath (Join-Path $mutationOutput 'sddc-spec.json') -Raw | ConvertFrom-Json
    $mutatedPlan = Get-Content -LiteralPath (Join-Path $mutationOutput 'migration-plan.json') -Raw | ConvertFrom-Json
    Assert-Equal $mutatedSddc.sddcId 'chi01-m02' 'New-VcfArchitecture ignores the design-requirements input'
    Assert-Equal $mutatedPlan.inventoryId 'mutation-inventory' 'New-VcfArchitecture ignores the inventory input'
    Assert-Equal $mutatedPlan.targetDesign.capacity.minimumWorkloadHostCount 4 'New-VcfArchitecture does not recalculate mutated workload demand'
    $mutatedEsxStep = @($mutatedPlan.steps | Where-Object { $_.componentType -eq 'ESX_HOST' })[0]
    Assert-Equal $mutatedEsxStep.targetBuild '25379999' 'New-VcfArchitecture ignores target builds in the compatibility snapshot'
    Assert-Equal $mutatedEsxStep.compatibilityReason 'MUTATION_PROBE' 'New-VcfArchitecture ignores path outcomes in the compatibility snapshot'
}
finally {
    if ($null -ne $moduleUnderTest) {
        Remove-Module -ModuleInfo $moduleUnderTest -Force -ErrorAction SilentlyContinue
    }
    foreach ($stubName in $sdkStubNames) {
        Remove-Item -Path ("Function:\global:" + $stubName) -Force -ErrorAction SilentlyContinue
    }
    Remove-Variable -Name VcfArchitectureValidationCalls -Scope Global -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $verificationRoot) {
        Remove-Item -LiteralPath $verificationRoot -Recurse -Force
    }
}

Write-Output 'VERIFICATION PASSED: research, installer schema, architecture, compatibility, capacity, migration, and PowerCLI integration are valid.'
