[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Stop-Verification {
    param([Parameter(Mandatory)][string]$Message)
    throw "VERIFICATION FAILED: $Message"
}

function Assert-True {
    param(
        [Parameter(Mandatory)][bool]$Condition,
        [Parameter(Mandatory)][string]$Message
    )
    if (-not $Condition) {
        Stop-Verification $Message
    }
}

function Read-Json {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Stop-Verification "Missing JSON file: $Path"
    }
    try {
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -Depth 100
    }
    catch {
        Stop-Verification "Invalid JSON in $Path`: $($_.Exception.Message)"
    }
}

function Get-CanonicalJson {
    param([Parameter(Mandatory)][string]$Path)
    return (Read-Json $Path) | ConvertTo-Json -Depth 100 -Compress
}

function Assert-SetEqual {
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Actual,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Expected,
        [Parameter(Mandatory)][string]$Message
    )
    $actualValues = @($Actual | ForEach-Object { [string]$_ } | Sort-Object -Unique)
    $expectedValues = @($Expected | ForEach-Object { [string]$_ } | Sort-Object -Unique)
    if (($actualValues -join "`n") -cne ($expectedValues -join "`n")) {
        Stop-Verification "$Message (actual: $($actualValues -join ', '); expected: $($expectedValues -join ', '))"
    }
}

$sddcPath = Join-Path $repositoryRoot 'architecture/greenfield-sddc-spec.json'
$openApiPath = Join-Path $repositoryRoot 'specifications/vcf-installer/vcf-installer-openapi.json'
if (-not (Test-Path -LiteralPath $sddcPath -PathType Leaf)) {
    Stop-Verification 'architecture/greenfield-sddc-spec.json is missing.'
}
if (-not (Test-Path -LiteralPath $openApiPath -PathType Leaf)) {
    Stop-Verification 'The pinned installer OpenAPI document is missing.'
}

# The first substantive check is validation of the submitted artifact against
# the SddcSpec schema carried by the pinned installer OpenAPI document.
try {
    $sddcRaw = Get-Content -LiteralPath $sddcPath -Raw
    $openApi = Get-Content -LiteralPath $openApiPath -Raw | ConvertFrom-Json -AsHashtable -Depth 100
    $sddcSchema = [ordered]@{
        '$schema' = 'http://json-schema.org/draft-07/schema#'
        '$ref' = '#/components/schemas/SddcSpec'
        components = $openApi.components
    } | ConvertTo-Json -Depth 100
    $schemaValid = Test-Json -Json $sddcRaw -Schema $sddcSchema -ErrorAction Stop
    if (-not $schemaValid) {
        Stop-Verification 'greenfield-sddc-spec.json does not validate as the installer OpenAPI SddcSpec.'
    }
}
catch {
    Stop-Verification "SddcSpec schema validation failed: $($_.Exception.Message)"
}

$protectedHashes = [ordered]@{
    'fixtures/site-requirements.json' = '750436c6354398b7cac7c56e8d68a4c76982c99c8c8ee96f1fc76d084e71903c'
    'fixtures/estate-inventory.json' = '9f56634f12a73eea717267ab96cbd7b29851ffd9641ba21d764fa3b0120e2c74'
    'fixtures/compatibility-snapshot.json' = '3d26370a92a86744b015a45585d53c66516ab3b7bbd26a3808d3b5ec24ad3386'
    'schemas/migration-plan.schema.json' = '3b0115c2aa49aa9579d7450b1a1e2a1ea5e160e7d7918db2894a0ea7efc0f4e9'
    'specifications/vcf-installer/SOURCE.json' = 'a5d430d78f897c0666f1a639ad2e718d8f81cc58a1c68f4a05a7a7dd29db83e5'
    'specifications/vcf-installer/LICENSE' = 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30'
    'specifications/vcf-installer/vcf-installer-openapi.json' = 'a2084a65aab0ac0a5a1625d1a2fdf20b55fc8895ca43fd4389da901d07a4aaef'
}
foreach ($relativePath in $protectedHashes.Keys) {
    $path = Join-Path $repositoryRoot $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Stop-Verification "Protected file is missing: $relativePath"
    }
    $actualHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -cne $protectedHashes[$relativePath]) {
        Stop-Verification "Protected file was modified: $relativePath"
    }
}

$requirements = Read-Json (Join-Path $repositoryRoot 'fixtures/site-requirements.json')
$inventory = Read-Json (Join-Path $repositoryRoot 'fixtures/estate-inventory.json')
$compatibility = Read-Json (Join-Path $repositoryRoot 'fixtures/compatibility-snapshot.json')
$sddc = Read-Json $sddcPath

$cpuHosts = [Math]::Ceiling(
    [double]$requirements.capacity.workloadVcpu /
    ([double]$requirements.capacity.hostProfile.cores * ([double]$requirements.capacity.maximumCpuUtilizationPercent / 100.0))
) + [int]$requirements.availability.hostFailuresToTolerate
$memoryHosts = [Math]::Ceiling(
    [double]$requirements.capacity.workloadMemoryGiB /
    ([double]$requirements.capacity.hostProfile.memoryGiB * ([double]$requirements.capacity.maximumMemoryUtilizationPercent / 100.0))
) + [int]$requirements.availability.hostFailuresToTolerate
$storageHosts = [Math]::Ceiling(
    ([double]$requirements.capacity.workloadUsableStorageTiB * [double]$requirements.capacity.storageProtectionOverhead) /
    ([double]$requirements.capacity.hostProfile.rawStorageTiB * (1.0 - ([double]$requirements.capacity.storageFreeSpaceReservePercent / 100.0)))
) + [int]$requirements.availability.hostFailuresToTolerate
$requiredHostCount = [Math]::Max(
    [int]$compatibility.greenfield.supportedInstallerCombination.minimumManagementDomainHosts,
    [Math]::Max([int]$cpuHosts, [Math]::Max([int]$memoryHosts, [int]$storageHosts))
)

Assert-True ($sddc.sddcId -ceq $requirements.target.sddcId) 'The SDDC identifier does not match the site contract.'
Assert-True ($sddc.workflowType -ceq $requirements.target.workflowType) 'The workflow must be the greenfield VCF workflow.'
Assert-True ($sddc.version -ceq $compatibility.targetVcfVersion) 'The SddcSpec target version is not the pinned VCF version.'
Assert-True ($sddc.vcfInstanceName -ceq $requirements.target.vcfInstanceName) 'The VCF instance name does not match the site contract.'
Assert-True (@($sddc.hostSpecs).Count -eq $requiredHostCount) "The management domain must use the calculated minimum of $requiredHostCount hosts."
Assert-True (@($sddc.hostSpecs.hostname | Sort-Object -Unique).Count -eq $requiredHostCount) 'Management-domain hostnames must be unique.'
$expectedHostnames = @(1..$requiredHostCount | ForEach-Object { '{0}{1:d2}' -f $requirements.names.hostPrefix, $_ })
Assert-SetEqual @($sddc.hostSpecs.hostname) $expectedHostnames 'Management-domain hostnames do not follow the site naming contract.'
Assert-True ($sddc.skipEsxThumbprintValidation -eq $false) 'ESXi thumbprint validation must remain enabled.'
Assert-True ($sddc.skipGatewayPingValidation -eq $false) 'Gateway ping validation must remain enabled.'
Assert-True ($sddc.vcenterSpec.version -ceq $compatibility.greenfield.supportedInstallerCombination.vcenter) 'vCenter is not on the pinned supported combination.'
Assert-True ($sddc.vcenterSpec.vcenterHostname -ceq $requirements.names.vcenter) 'The vCenter hostname does not match the site contract.'
Assert-True ($sddc.vcenterSpec.rootVcenterPassword -ceq $requirements.installerPlaceholders.rootVcenterPassword) 'The supplied deployment-time vCenter password placeholder was not preserved.'
Assert-True ($sddc.vcenterSpec.useExistingDeployment -eq $false) 'The greenfield specification must deploy a new vCenter.'
Assert-True ($sddc.nsxtSpec.version -ceq $compatibility.greenfield.supportedInstallerCombination.nsx) 'NSX is not on the pinned supported combination.'
Assert-True (@($sddc.nsxtSpec.nsxtManagers).Count -eq [int]$compatibility.greenfield.supportedInstallerCombination.nsxManagerCountForHa) 'The NSX manager count does not meet the HA contract.'
Assert-SetEqual @($sddc.nsxtSpec.nsxtManagers.hostname) @($requirements.names.nsxManagers) 'NSX manager hostnames do not match the site contract.'
Assert-True ($sddc.nsxtSpec.vipFqdn -ceq $requirements.names.nsxVip) 'The NSX VIP does not match the site contract.'
Assert-True ($sddc.nsxtSpec.useExistingDeployment -eq $false) 'The greenfield specification must deploy a new NSX instance.'
Assert-True (@($sddc.vcfOperationsSpec.nodes).Count -eq [int]$compatibility.greenfield.supportedInstallerCombination.operationsNodeCountForHa) 'The VCF Operations node count does not meet the HA contract.'
Assert-SetEqual @($sddc.vcfOperationsSpec.nodes | ForEach-Object { "$($_.hostname):$($_.type)" }) @($requirements.names.operationsNodes | ForEach-Object { "$($_.hostname):$($_.type)" }) 'VCF Operations node placement does not match the site contract.'
Assert-True ($sddc.vcfOperationsSpec.loadBalancerFqdn -ceq $requirements.names.operationsLoadBalancer) 'The VCF Operations load balancer does not match the site contract.'
Assert-True ($sddc.vcfOperationsSpec.version -ceq $compatibility.targetVcfVersion) 'VCF Operations is not on the pinned target version.'
Assert-True ($sddc.vcfOperationsSpec.useExistingDeployment -eq $false) 'The greenfield specification must deploy a new VCF Operations instance.'
Assert-True ($sddc.sddcManagerSpec.hostname -ceq $requirements.names.sddcManager) 'The SDDC Manager hostname does not match the site contract.'
Assert-True ($sddc.sddcManagerSpec.version -ceq $compatibility.targetVcfVersion) 'SDDC Manager is not on the pinned target version.'
Assert-True ($sddc.sddcManagerSpec.useExistingDeployment -eq $false) 'The greenfield specification must deploy a new SDDC Manager.'
Assert-True ($sddc.dnsSpec.subdomain -ceq $requirements.site.dnsSubdomain) 'The DNS subdomain does not match the site contract.'
Assert-SetEqual @($sddc.dnsSpec.nameservers) @($requirements.site.dnsServers) 'DNS servers do not match the site contract.'
Assert-SetEqual @($sddc.ntpServers) @($requirements.site.ntpServers) 'NTP servers do not match the site contract.'
Assert-True ($sddc.datastoreSpec.vsanSpec.esaConfig.enabled -eq $true) 'The supported vSAN ESA storage architecture was not selected.'
Assert-True ([int]$sddc.datastoreSpec.vsanSpec.failuresToTolerate -eq [int]$requirements.availability.hostFailuresToTolerate) 'vSAN failures-to-tolerate does not match the availability contract.'

Assert-True (@($sddc.networkSpecs).Count -eq @($requirements.networks).Count) 'The SddcSpec must contain every required network exactly once.'
foreach ($expectedNetwork in $requirements.networks) {
    $actualNetwork = @($sddc.networkSpecs | Where-Object networkType -CEQ $expectedNetwork.networkType)
    Assert-True ($actualNetwork.Count -eq 1) "Network $($expectedNetwork.networkType) is missing or duplicated."
    $actualNetwork = $actualNetwork[0]
    Assert-True ([int]$actualNetwork.vlanId -eq [int]$expectedNetwork.vlanId) "VLAN mismatch for $($expectedNetwork.networkType)."
    Assert-True ($actualNetwork.subnet -ceq $expectedNetwork.subnet) "Subnet mismatch for $($expectedNetwork.networkType)."
    Assert-True ($actualNetwork.gateway -ceq $expectedNetwork.gateway) "Gateway mismatch for $($expectedNetwork.networkType)."
    Assert-True ($actualNetwork.subnetMask -ceq $expectedNetwork.subnetMask) "Subnet-mask mismatch for $($expectedNetwork.networkType)."
    Assert-True ([int]$actualNetwork.mtu -eq [int]$expectedNetwork.mtu) "MTU mismatch for $($expectedNetwork.networkType)."
    Assert-True (@($actualNetwork.includeIpAddressRanges).Count -eq 1) "Network $($expectedNetwork.networkType) must have exactly one supplied address range."
    Assert-True ($actualNetwork.includeIpAddressRanges[0].startIpAddress -ceq $expectedNetwork.start) "Address-range start mismatch for $($expectedNetwork.networkType)."
    Assert-True ($actualNetwork.includeIpAddressRanges[0].endIpAddress -ceq $expectedNetwork.end) "Address-range end mismatch for $($expectedNetwork.networkType)."
}

Assert-True (@($sddc.dvsSpecs).Count -eq @($requirements.distributedSwitches).Count) 'The SddcSpec must contain both required distributed switches.'
foreach ($expectedDvs in $requirements.distributedSwitches) {
    $actualDvs = @($sddc.dvsSpecs | Where-Object dvsName -CEQ $expectedDvs.name)
    Assert-True ($actualDvs.Count -eq 1) "Distributed switch $($expectedDvs.name) is missing or duplicated."
    $actualDvs = $actualDvs[0]
    Assert-True ([int]$actualDvs.mtu -eq [int]$expectedDvs.mtu) "MTU mismatch on $($expectedDvs.name)."
    Assert-SetEqual @($actualDvs.networks) @($expectedDvs.networks) "Network membership mismatch on $($expectedDvs.name)."
    $actualMappings = @($actualDvs.vmnicsToUplinks | ForEach-Object { "$($_.id):$($_.uplink)" })
    $expectedMappings = @($expectedDvs.vmnics | ForEach-Object { "$($_.id):$($_.uplink)" })
    Assert-SetEqual $actualMappings $expectedMappings "Physical uplink mapping mismatch on $($expectedDvs.name)."
}

$tepNetwork = @($requirements.networks | Where-Object networkType -CEQ 'HOST_TEP')[0]
Assert-True ([int]$sddc.nsxtSpec.transportVlanId -eq [int]$tepNetwork.vlanId) 'The NSX host TEP transport VLAN is wrong.'
Assert-True (@($sddc.nsxtSpec.ipAddressPoolSpec.subnets).Count -eq 1) 'The NSX host TEP pool must contain one supplied subnet.'
$tepSubnet = $sddc.nsxtSpec.ipAddressPoolSpec.subnets[0]
Assert-True ($tepSubnet.cidr -ceq $tepNetwork.subnet) 'The NSX host TEP pool CIDR is wrong.'
Assert-True ($tepSubnet.gateway -ceq $tepNetwork.gateway) 'The NSX host TEP pool gateway is wrong.'
Assert-True (@($tepSubnet.ipAddressPoolRanges).Count -eq 1) 'The NSX host TEP pool must contain one supplied address range.'
Assert-True ($tepSubnet.ipAddressPoolRanges[0].start -ceq $tepNetwork.start) 'The NSX host TEP pool start address is wrong.'
Assert-True ($tepSubnet.ipAddressPoolRanges[0].end -ceq $tepNetwork.end) 'The NSX host TEP pool end address is wrong.'

$edgePath = Join-Path $repositoryRoot 'architecture/greenfield-edge-design.json'
$edge = Read-Json $edgePath
$requiredThroughput = [double]$requirements.edgeTraffic.requiredNorthSouthGbps
$selectedForm = @($compatibility.greenfield.edgeFormFactors | Where-Object { [double]$_.planningThroughputGbpsPerNode -ge $requiredThroughput } | Select-Object -First 1)
Assert-True ($selectedForm.Count -eq 1) 'The pinned snapshot has no Edge form factor capable of the required failure-state throughput.'
$selectedForm = $selectedForm[0]
Assert-True ($edge.architectureId -ceq $requirements.architectureId) 'The Edge design architecture identifier is wrong.'
Assert-True ($edge.siteId -ceq $requirements.site.id) 'The Edge design site identifier is wrong.'
Assert-True ([double]$edge.requirement.requiredNorthSouthGbps -eq $requiredThroughput) 'The Edge design does not carry the throughput requirement.'
Assert-True ($edge.edgeCluster.formFactor -ceq $selectedForm.name) 'The Edge form factor is not the smallest supported size that survives one-node failure at the required throughput.'
Assert-True ([double]$edge.edgeCluster.planningThroughputGbpsPerNode -eq [double]$selectedForm.planningThroughputGbpsPerNode) 'The Edge per-node planning throughput is wrong.'
Assert-True ([int]$edge.edgeCluster.nodeCount -eq [int]$compatibility.greenfield.edgeCluster.minimumNodesForOneNodeFailure) 'The Edge cluster node count is wrong.'
Assert-True ($edge.edgeCluster.haMode -ceq $compatibility.greenfield.edgeCluster.haMode) 'The Edge HA mode is wrong.'
Assert-True ($edge.edgeCluster.routingMode -ceq $compatibility.greenfield.edgeCluster.routingMode) 'The Edge routing mode is wrong.'
Assert-True (@($edge.edgeCluster.nodes.placementFailureDomain | Sort-Object -Unique).Count -eq [int]$compatibility.greenfield.edgeCluster.requiredDistinctFailureDomains) 'Edge nodes are not placed in distinct failure domains.'
Assert-SetEqual @($edge.edgeCluster.nodes.placementFailureDomain) @($requirements.site.failureDomains) 'Edge-node placements do not use the required site failure domains.'
Assert-True ($edge.requirement.survivesSingleEdgeNodeFailure -eq [bool]$requirements.edgeTraffic.requiredAfterSingleEdgeFailure) 'The Edge design does not carry the single-node failure requirement.'
Assert-True ($edge.requirement.survivesSingleTorFailure -eq [bool]$requirements.edgeTraffic.requiredAfterSingleTorFailure) 'The Edge design does not carry the single-ToR failure requirement.'

Assert-True (@($edge.uplinks).Count -eq [int]$selectedForm.dataPathUplinksPerNode) 'The Edge design must have two independent data-path uplinks.'
Assert-True (@($edge.uplinks.tor | Sort-Object -Unique).Count -eq [int]$compatibility.greenfield.edgeCluster.requiredDistinctTorsPerNode) 'The Edge uplinks must terminate on distinct ToR switches.'
$edgeNetworks = @($requirements.networks | Where-Object { $_.networkType -in @('EDGE_UPLINK_A', 'EDGE_UPLINK_B') })
$backingDvs = @($requirements.distributedSwitches | Where-Object name -CEQ $compatibility.greenfield.edgeCluster.backingVds)[0]
Assert-SetEqual @($edge.uplinks.hostVmnic) @($backingDvs.vmnics.id) 'Edge uplinks are not backed by the required VDS physical NICs.'
Assert-SetEqual @($edge.uplinks.vlanId) @($edgeNetworks.vlanId) 'Edge uplinks do not use both required uplink VLANs exactly once.'
foreach ($uplink in $edge.uplinks) {
    $hostNic = @($requirements.capacity.hostProfile.physicalNics | Where-Object id -CEQ $uplink.hostVmnic)
    Assert-True ($hostNic.Count -eq 1) "Unknown Edge host vmnic $($uplink.hostVmnic)."
    Assert-True ($uplink.tor -ceq $hostNic[0].tor) "ToR mismatch for Edge host vmnic $($uplink.hostVmnic)."
    Assert-True ([int]$uplink.speedGbps -eq [int]$hostNic[0].speedGbps) "Speed mismatch for Edge host vmnic $($uplink.hostVmnic)."
    Assert-True ([double]$uplink.speedGbps -ge $requiredThroughput) "Edge uplink $($uplink.name) cannot carry the failure-state throughput."
    Assert-True ([int]$uplink.speedGbps -in @($selectedForm.supportedUplinkSpeedsGbps | ForEach-Object { [int]$_ })) "Edge uplink $($uplink.name) uses a speed unsupported by the selected form factor."
    Assert-True ($uplink.backingVds -ceq $compatibility.greenfield.edgeCluster.backingVds) "Backing VDS mismatch for Edge uplink $($uplink.name)."
    $edgeNetwork = @($edgeNetworks | Where-Object vlanId -EQ ([int]$uplink.vlanId))
    Assert-True ($edgeNetwork.Count -eq 1) "Unsupported VLAN on Edge uplink $($uplink.name)."
    Assert-True ([int]$uplink.mtu -eq [int]$edgeNetwork[0].mtu) "MTU mismatch on Edge uplink $($uplink.name)."
}

$migrationPath = Join-Path $repositoryRoot 'architecture/estate-migration-plan.json'
if (-not (Test-Path -LiteralPath $migrationPath -PathType Leaf)) {
    Stop-Verification 'architecture/estate-migration-plan.json is missing.'
}
$migrationRaw = Get-Content -LiteralPath $migrationPath -Raw
$migrationSchemaPath = Join-Path $repositoryRoot 'schemas/migration-plan.schema.json'
try {
    $migrationSchemaValid = Test-Json -Json $migrationRaw -SchemaFile $migrationSchemaPath -ErrorAction Stop
    if (-not $migrationSchemaValid) {
        Stop-Verification 'estate-migration-plan.json does not validate against migration-plan.schema.json.'
    }
}
catch {
    Stop-Verification "Migration-plan schema validation failed: $($_.Exception.Message)"
}
$migration = Read-Json $migrationPath
Assert-True ($migration.schemaVersion -ceq '1.0') 'Migration schemaVersion must be 1.0.'
Assert-True ($migration.estateId -ceq $inventory.estateId) 'Migration estateId does not match the inventory.'
Assert-True ($migration.targetVcfVersion -ceq $compatibility.targetVcfVersion) 'Migration target VCF version is wrong.'
Assert-True (@($migration.steps).Count -eq @($inventory.components).Count) 'The migration plan must contain every inventory component exactly once.'
Assert-SetEqual @($migration.steps.componentId) @($inventory.components.id) 'Migration component coverage does not match the estate inventory.'

$orders = @($migration.steps.order | ForEach-Object { [int]$_ } | Sort-Object)
$expectedOrders = @(1..@($inventory.components).Count)
Assert-SetEqual $orders $expectedOrders 'Migration step order must be unique and contiguous.'
$stepsById = @{}
foreach ($step in $migration.steps) {
    if ($stepsById.ContainsKey($step.componentId)) {
        Stop-Verification "Duplicate migration component: $($step.componentId)"
    }
    $stepsById[$step.componentId] = $step
}

$lastPhase = -1
foreach ($step in @($migration.steps | Sort-Object order)) {
    $component = @($inventory.components | Where-Object id -CEQ $step.componentId)[0]
    $transition = @($compatibility.migrationTransitions | Where-Object componentId -CEQ $step.componentId)[0]
    Assert-True ($null -ne $component) "Migration step references an unknown inventory component: $($step.componentId)."
    Assert-True ($null -ne $transition) "No pinned compatibility transition exists for $($step.componentId)."
    Assert-True ($step.source.name -ceq $component.name) "Source name mismatch for $($step.componentId)."
    Assert-True ($step.source.version -ceq $component.version) "Source version mismatch for $($step.componentId)."
    Assert-True ($step.target.name -ceq $transition.targetName) "Target name mismatch for $($step.componentId)."
    Assert-True ($step.target.version -ceq $transition.targetVersion) "Target version mismatch for $($step.componentId)."
    Assert-True ($step.action -ceq $transition.action) "Transition action mismatch for $($step.componentId)."
    Assert-True ([int]$transition.phase -ge $lastPhase) "The migration order violates the pinned lifecycle sequence at $($step.componentId)."
    $lastPhase = [int]$transition.phase

    $actualGates = @($step.gates | ForEach-Object { "$($_.componentId):$($_.requiredVersion)" })
    $expectedGates = @($transition.requires | ForEach-Object { "$($_.componentId):$($_.requiredVersion)" })
    Assert-SetEqual $actualGates $expectedGates "Technical gates mismatch for $($step.componentId)."
    foreach ($gate in $step.gates) {
        Assert-True ($stepsById.ContainsKey($gate.componentId)) "Gate $($gate.componentId) for $($step.componentId) is not an inventory component."
        Assert-True ([int]$stepsById[$gate.componentId].order -lt [int]$step.order) "Gate $($gate.componentId) must be an earlier migration step than $($step.componentId)."
    }
}

$researchPath = Join-Path $repositoryRoot 'research-sources.md'
if (-not (Test-Path -LiteralPath $researchPath -PathType Leaf)) {
    Stop-Verification 'research-sources.md is missing.'
}
$researchContent = Get-Content -LiteralPath $researchPath -Raw
Assert-True ($researchContent -cmatch '(?im)\b(access(ed)?|consulted)\b' -and $researchContent -cmatch '\b20\d{2}\b') 'The research log must record a source access date.'
$urlMatches = @([regex]::Matches($researchContent, 'https?://[^\s)>|]+'))
Assert-True ($urlMatches.Count -gt 0) 'The research log contains no source URL.'
foreach ($urlMatch in $urlMatches) {
    $urlText = $urlMatch.Value.TrimEnd('.', ',')
    try {
        $uri = [Uri]$urlText
    }
    catch {
        Stop-Verification "The research log contains an invalid URL: $urlText"
    }
    Assert-True ($uri.Scheme -in @('http', 'https')) "Research source is not an HTTP(S) URL: $urlText"
    $publishedByBroadcom = `
        $uri.Host -ceq 'broadcom.com' -or `
        $uri.Host.EndsWith('.broadcom.com', [StringComparison]::OrdinalIgnoreCase) -or `
        $uri.Host -ceq 'vmware.com' -or `
        $uri.Host.EndsWith('.vmware.com', [StringComparison]::OrdinalIgnoreCase) -or `
        ($uri.Host -ceq 'github.com' -and $uri.AbsolutePath.StartsWith('/vmware/', [StringComparison]::OrdinalIgnoreCase))
    Assert-True $publishedByBroadcom "Research source is not Broadcom-published: $urlText"

}
$researchTerms = $researchContent.ToLowerInvariant()
foreach ($term in @('vcf', 'esxi', 'vcenter', 'nsx')) {
    Assert-True ($researchTerms.Contains($term)) "The research log does not cover the supported VCF/ESXi/vCenter/NSX combination ($term is missing)."
}
Assert-True ($researchTerms.Contains('edge') -and $researchTerms.Contains('form factor') -and ($researchTerms.Contains('throughput') -or $researchTerms.Contains('datapath'))) 'The research log does not record an Edge sizing conclusion.'
Assert-True ($researchTerms.Contains('5.2') -and $researchTerms.Contains('9.0') -and ($researchTerms.Contains('upgrade') -or $researchTerms.Contains('update') -or $researchTerms.Contains('migration') -or $researchTerms.Contains('transition'))) 'The research log does not record the estate upgrade-path conclusion.'

$moduleManifest = Join-Path $repositoryRoot 'src/VcfArchitecture/VcfArchitecture.psd1'
$temporaryOutput = Join-Path ([System.IO.Path]::GetTempPath()) ("vcfarch-verify-$([Guid]::NewGuid().ToString('N'))")
try {
    $manifestData = Import-PowerShellDataFile -LiteralPath $moduleManifest
    Assert-True ($manifestData.RootModule -ceq 'VcfArchitecture.psm1') 'The module manifest has the wrong root module.'
    $requiredModuleNames = @($manifestData.RequiredModules | ForEach-Object {
        if ($_ -is [string]) { $_ } else { $_.ModuleName }
    })
    $installerRequirements = @($requiredModuleNames | Where-Object { $_ -ceq 'VMware.Sdk.Vcf.Installer' })
    Assert-True ($installerRequirements.Count -eq 1) 'The module manifest does not require VMware.Sdk.Vcf.Installer exactly once.'
    Assert-SetEqual @($manifestData.FunctionsToExport) @('New-VcfGreenfieldSddcSpec', 'New-VcfEstateMigrationPlan', 'Export-VcfArchitecture') 'The module manifest exports the wrong public functions.'

    Import-Module $moduleManifest -Force -ErrorAction Stop
    New-Item -ItemType Directory -Path $temporaryOutput -Force | Out-Null
    $runtimeSddc = New-VcfGreenfieldSddcSpec `
        -RequirementsPath (Join-Path $repositoryRoot 'fixtures/site-requirements.json') `
        -CompatibilitySnapshotPath (Join-Path $repositoryRoot 'fixtures/compatibility-snapshot.json')
    Assert-True ($runtimeSddc.GetType().FullName -ceq 'VMware.Bindings.Vcf.Installer.Model.SddcSpec') 'New-VcfGreenfieldSddcSpec did not return the genuine VMware SDK SddcSpec type.'

    $runtimeMigration = New-VcfEstateMigrationPlan `
        -InventoryPath (Join-Path $repositoryRoot 'fixtures/estate-inventory.json') `
        -CompatibilitySnapshotPath (Join-Path $repositoryRoot 'fixtures/compatibility-snapshot.json')
    $runtimeMigrationJson = $runtimeMigration | ConvertTo-Json -Depth 100 -Compress
    if ($runtimeMigrationJson -cne (Get-CanonicalJson $migrationPath)) {
        Stop-Verification 'New-VcfEstateMigrationPlan does not return the committed migration plan.'
    }

    $exportedPaths = Export-VcfArchitecture `
        -RequirementsPath (Join-Path $repositoryRoot 'fixtures/site-requirements.json') `
        -InventoryPath (Join-Path $repositoryRoot 'fixtures/estate-inventory.json') `
        -CompatibilitySnapshotPath (Join-Path $repositoryRoot 'fixtures/compatibility-snapshot.json') `
        -OutputPath $temporaryOutput

    Assert-SetEqual @($exportedPaths.SddcSpec, $exportedPaths.EdgeDesign, $exportedPaths.MigrationPlan) @(
        (Join-Path $temporaryOutput 'greenfield-sddc-spec.json'),
        (Join-Path $temporaryOutput 'greenfield-edge-design.json'),
        (Join-Path $temporaryOutput 'estate-migration-plan.json')
    ) 'Export-VcfArchitecture did not return all generated artifact paths.'

    foreach ($artifactName in @('greenfield-sddc-spec.json', 'greenfield-edge-design.json', 'estate-migration-plan.json')) {
        $committed = Get-CanonicalJson (Join-Path $repositoryRoot "architecture/$artifactName")
        $generated = Get-CanonicalJson (Join-Path $temporaryOutput $artifactName)
        if ($generated -cne $committed) {
            Stop-Verification "$artifactName is not reproducible from the supplied fixtures by Export-VcfArchitecture."
        }
        try {
            $null = [Text.UTF8Encoding]::new($false, $true).GetString([IO.File]::ReadAllBytes((Join-Path $temporaryOutput $artifactName)))
        }
        catch {
            Stop-Verification "$artifactName was not written as valid UTF-8."
        }
    }
}
catch {
    Stop-Verification "PowerShell module execution failed: $($_.Exception.Message)"
}
finally {
    if (Test-Path -LiteralPath $temporaryOutput) {
        Remove-Item -LiteralPath $temporaryOutput -Recurse -Force
    }
}

Write-Host 'Verification passed: SddcSpec schema, protected inputs, greenfield architecture, Edge sizing/uplinks, migration plan, and module reproducibility.'
