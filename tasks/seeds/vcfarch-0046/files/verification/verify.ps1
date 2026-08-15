[CmdletBinding()]
param(
    [Parameter()]
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Stop-Verification {
    param([Parameter(Mandatory)][string]$Message)
    throw "VERIFICATION FAILED: $Message"
}

function Assert-Equal {
    param(
        [Parameter(Mandatory)]$Actual,
        [Parameter(Mandatory)]$Expected,
        [Parameter(Mandatory)][string]$Message
    )
    if ($Actual -ne $Expected) {
        Stop-Verification "$Message (expected '$Expected', got '$Actual')"
    }
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

function Assert-SetEqual {
    param(
        [AllowEmptyCollection()][object[]]$Actual,
        [AllowEmptyCollection()][object[]]$Expected,
        [Parameter(Mandatory)][string]$Message
    )
    $actualValues = @($Actual | ForEach-Object { [string]$_ } | Sort-Object -Unique)
    $expectedValues = @($Expected | ForEach-Object { [string]$_ } | Sort-Object -Unique)
    if (($actualValues.Count -ne $expectedValues.Count) -or
        (Compare-Object -ReferenceObject $expectedValues -DifferenceObject $actualValues)) {
        Stop-Verification "$Message (expected [$($expectedValues -join ', ')], got [$($actualValues -join ', ')])"
    }
}

function Assert-NonEmptyString {
    param(
        [AllowNull()]$Value,
        [Parameter(Mandatory)][string]$Message
    )
    if ($Value -isnot [string] -or [string]::IsNullOrWhiteSpace($Value)) {
        Stop-Verification $Message
    }
}

function Assert-SecretPlaceholder {
    param(
        [AllowNull()]$Value,
        [Parameter(Mandatory)][string]$Message
    )
    if ($Value -isnot [string] -or $Value -notmatch '^<[A-Z][A-Z0-9_]*_(SECRET|KEY)>$') {
        Stop-Verification "$Message (got '$Value')"
    }
}

# The upstream SddcSpec schema is intentionally the first verification gate.
# No fixture, compatibility snapshot, module source, migration plan, or research
# record is opened until this gate passes.
$sddcSpecPath = Join-Path $RepositoryRoot 'output/sddc-spec.json'
$installerOpenApiPath = Join-Path $RepositoryRoot 'specifications/vcf-installer/vcf-installer-openapi.json'
if (-not (Test-Path -LiteralPath $sddcSpecPath -PathType Leaf)) {
    Stop-Verification 'output/sddc-spec.json is missing, so upstream schema validation cannot run'
}
if (-not (Test-Path -LiteralPath $installerOpenApiPath -PathType Leaf)) {
    Stop-Verification 'the pinned VCF Installer OpenAPI document is missing'
}

$sddcSpecJson = Get-Content -LiteralPath $sddcSpecPath -Raw
$installerOpenApi = Get-Content -LiteralPath $installerOpenApiPath -Raw | ConvertFrom-Json -Depth 100
$sddcSchema = [ordered]@{
    '$schema' = 'http://json-schema.org/draft-07/schema#'
    '$ref' = '#/components/schemas/SddcSpec'
    components = $installerOpenApi.components
} | ConvertTo-Json -Depth 100

try {
    $sddcSchemaValid = Test-Json -Json $sddcSpecJson -Schema $sddcSchema -ErrorAction Stop
}
catch {
    Stop-Verification "output/sddc-spec.json does not validate as components.schemas.SddcSpec from the pinned installer specification: $($_.Exception.Message)"
}
if (-not $sddcSchemaValid) {
    Stop-Verification 'output/sddc-spec.json does not validate as components.schemas.SddcSpec from the pinned installer specification'
}

# Only after the upstream schema passes may deterministic local checks begin.
$protectedInputs = [ordered]@{
    'specifications/vcf-installer/vcf-installer-openapi.json' = 'a2084a65aab0ac0a5a1625d1a2fdf20b55fc8895ca43fd4389da901d07a4aaef'
    'fixtures/design-requirements.json' = '3ffe7613aa545b693ad8c0ec5b49379d9e5d0ad852be2eefa6c579fcf737d9ee'
    'fixtures/estate-inventory.json' = '7ce268bcfa16682b163633429eefcbd83ca15d7b7a26250a6ebcfbdbdb44b034'
    'verification/authority/compatibility-snapshot.json' = '9f313b1d9096ac7ef2c3b7b133a4acacda9d3af3ff6d27f4b52951c67aa6fea6'
    'schemas/migration-plan.schema.json' = '779c8bc5485a60300c01e63bbe770329675c92b6c5c453c9c29eada9fdaf8c1f'
}
foreach ($relativePath in $protectedInputs.Keys) {
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $RepositoryRoot $relativePath)).Hash.ToLowerInvariant()
    Assert-Equal $actualHash $protectedInputs[$relativePath] "protected verifier input changed: $relativePath"
}

$sddcSpec = $sddcSpecJson | ConvertFrom-Json -Depth 100
$requirements = Get-Content -LiteralPath (Join-Path $RepositoryRoot 'fixtures/design-requirements.json') -Raw | ConvertFrom-Json -Depth 100
$inventory = Get-Content -LiteralPath (Join-Path $RepositoryRoot 'fixtures/estate-inventory.json') -Raw | ConvertFrom-Json -Depth 100
$compatibility = Get-Content -LiteralPath (Join-Path $RepositoryRoot 'verification/authority/compatibility-snapshot.json') -Raw | ConvertFrom-Json -Depth 100

Assert-Equal $sddcSpec.version $requirements.target.version 'SddcSpec target version is wrong'
Assert-Equal $sddcSpec.workflowType $requirements.target.workflowType 'SddcSpec workflow type is wrong'
Assert-Equal $sddcSpec.sddcId $requirements.site.sddcId 'SddcSpec ID does not identify the required site'
Assert-Equal $sddcSpec.vcfInstanceName $requirements.site.vcfInstanceName 'VCF instance name is wrong'
Assert-Equal $sddcSpec.clusterSpec.datacenterName $requirements.site.datacenterName 'datacenter name is wrong'
Assert-Equal $sddcSpec.clusterSpec.clusterName $requirements.site.clusterName 'management cluster name is wrong'
Assert-True ($sddcSpecJson -notmatch [regex]::Escape([string]$requirements.site.legacySite)) 'SddcSpec must not contain or reuse the legacy site'

$selectedHostnames = @($sddcSpec.hostSpecs | ForEach-Object { $_.hostname })
$availableHostnames = @($requirements.hosts | ForEach-Object { $_.hostname })
Assert-SetEqual $selectedHostnames $availableHostnames 'SddcSpec must select every stated greenfield host exactly once'
Assert-True ($selectedHostnames.Count -eq $availableHostnames.Count) 'SddcSpec contains duplicate hosts'
Assert-True ($selectedHostnames.Count -ge [int]$requirements.capacity.minimumManagementHosts) 'management host count is below the requirement'

$selectedHosts = @($requirements.hosts | Where-Object { $_.hostname -in $selectedHostnames })
$rackCount = @($selectedHosts.rack | Sort-Object -Unique).Count
Assert-True ($rackCount -ge [int]$requirements.site.minimumRackCount) 'selected hosts do not span enough racks'

$totalCpuCores = ($selectedHosts | Measure-Object -Property cpuCores -Sum).Sum
$largestHostCpu = ($selectedHosts | Measure-Object -Property cpuCores -Maximum).Maximum
$survivingVcpu = ($totalCpuCores - $largestHostCpu) * [double]$requirements.capacity.maximumCpuOvercommitRatio
Assert-True ($survivingVcpu -ge [double]$requirements.capacity.requiredVcpu) 'N+1 CPU capacity is below demand'

$totalMemoryGiB = ($selectedHosts | Measure-Object -Property memoryGiB -Sum).Sum
$largestHostMemory = ($selectedHosts | Measure-Object -Property memoryGiB -Maximum).Maximum
$survivingMemoryGiB = $totalMemoryGiB - $largestHostMemory
Assert-True ($survivingMemoryGiB -ge [double]$requirements.capacity.requiredMemoryGiB) 'N+1 memory capacity is below demand'

$rawStorageTiB = ($selectedHosts | Measure-Object -Property rawStorageTiB -Sum).Sum
$usableStorageTiB = $rawStorageTiB * 0.5 * ([double]$requirements.capacity.maximumStorageUtilizationPercent / 100)
Assert-True ($usableStorageTiB -ge [double]$requirements.capacity.requiredUsableStorageTiB) 'usable FTT=1 storage capacity is below demand'

Assert-Equal $sddcSpec.vcenterSpec.vcenterHostname $requirements.appliances.vcenter 'vCenter FQDN is wrong'
Assert-Equal $sddcSpec.vcenterSpec.useExistingDeployment $false 'vCenter must be a greenfield deployment'
Assert-Equal $sddcSpec.sddcManagerSpec.hostname $requirements.appliances.sddcManager 'SDDC Manager FQDN is wrong'
Assert-Equal $sddcSpec.sddcManagerSpec.useExistingDeployment $false 'SDDC Manager must be a greenfield deployment'
Assert-Equal $sddcSpec.skipEsxThumbprintValidation $false 'ESXi thumbprint validation must remain enabled'
Assert-Equal $sddcSpec.skipGatewayPingValidation $false 'gateway ping validation must remain enabled'

Assert-SecretPlaceholder $sddcSpec.vcenterSpec.rootVcenterPassword 'vCenter root password must be a clearly named non-secret placeholder'
Assert-SecretPlaceholder $sddcSpec.vcenterSpec.adminUserSsoPassword 'SSO administrator password must be a clearly named non-secret placeholder'
Assert-SecretPlaceholder $sddcSpec.sddcManagerSpec.rootPassword 'SDDC Manager root password must be a clearly named non-secret placeholder'
Assert-SecretPlaceholder $sddcSpec.sddcManagerSpec.sshPassword 'SDDC Manager SSH password must be a clearly named non-secret placeholder'
Assert-SecretPlaceholder $sddcSpec.sddcManagerSpec.localUserPassword 'SDDC Manager local-user password must be a clearly named non-secret placeholder'

Assert-SetEqual @($sddcSpec.dnsSpec.nameservers) @($requirements.services.dnsServers) 'DNS server design is wrong'
Assert-Equal $sddcSpec.dnsSpec.subdomain $requirements.site.dnsSubdomain 'DNS subdomain is wrong'
Assert-SetEqual @($sddcSpec.ntpServers) @($requirements.services.ntpServers) 'NTP server design is wrong'
Assert-True (@($sddcSpec.dnsSpec.nameservers).Count -ge [int]$requirements.availability.minimumDnsServers) 'DNS is not redundant'
Assert-True (@($sddcSpec.ntpServers).Count -ge [int]$requirements.availability.minimumNtpServers) 'NTP is not redundant'

foreach ($requiredNetwork in $requirements.networks) {
    $matches = @($sddcSpec.networkSpecs | Where-Object { $_.networkType -eq $requiredNetwork.networkType })
    Assert-True ($matches.Count -eq 1) "network $($requiredNetwork.networkType) must occur exactly once"
    $network = $matches[0]
    Assert-Equal $network.vlanId $requiredNetwork.vlanId "$($requiredNetwork.networkType) VLAN is wrong"
    Assert-Equal $network.subnet $requiredNetwork.subnet "$($requiredNetwork.networkType) subnet is wrong"
    Assert-Equal $network.gateway $requiredNetwork.gateway "$($requiredNetwork.networkType) gateway is wrong"
    Assert-Equal $network.mtu $requiredNetwork.mtu "$($requiredNetwork.networkType) MTU is wrong"
    Assert-True (@($network.includeIpAddressRanges).Count -eq 1) "$($requiredNetwork.networkType) must contain one address range"
    Assert-Equal $network.includeIpAddressRanges[0].startIpAddress $requiredNetwork.rangeStart "$($requiredNetwork.networkType) range start is wrong"
    Assert-Equal $network.includeIpAddressRanges[0].endIpAddress $requiredNetwork.rangeEnd "$($requiredNetwork.networkType) range end is wrong"
}

Assert-True (@($sddcSpec.dvsSpecs).Count -ge 1) 'at least one distributed switch is required'
$mappedPnics = @($sddcSpec.dvsSpecs | ForEach-Object { $_.vmnicsToUplinks } | ForEach-Object { $_.id } | Sort-Object -Unique)
Assert-SetEqual $mappedPnics @('vmnic0', 'vmnic1') 'distributed switch must map both physical NICs'
Assert-True (@($sddcSpec.dvsSpecs | Where-Object { $_.mtu -eq 9000 }).Count -ge 1) 'distributed switch must use jumbo MTU'

Assert-SetEqual @($sddcSpec.nsxtSpec.nsxtManagers.hostname) @($requirements.appliances.nsxManagers) 'NSX Manager topology is wrong'
Assert-True (@($sddcSpec.nsxtSpec.nsxtManagers).Count -ge [int]$requirements.availability.minimumNsxManagers) 'NSX Manager topology is not highly available'
Assert-Equal $sddcSpec.nsxtSpec.vipFqdn $requirements.appliances.nsxVip 'NSX VIP is wrong'
Assert-Equal $sddcSpec.nsxtSpec.transportVlanId $requirements.nsxOverlay.vlanId 'NSX overlay VLAN is wrong'
Assert-Equal $sddcSpec.nsxtSpec.ipAddressPoolSpec.name $requirements.nsxOverlay.poolName 'NSX TEP pool name is wrong'
Assert-Equal $sddcSpec.nsxtSpec.ipAddressPoolSpec.subnets[0].cidr $requirements.nsxOverlay.cidr 'NSX TEP CIDR is wrong'
Assert-Equal $sddcSpec.nsxtSpec.ipAddressPoolSpec.subnets[0].gateway $requirements.nsxOverlay.gateway 'NSX TEP gateway is wrong'
Assert-Equal $sddcSpec.nsxtSpec.ipAddressPoolSpec.subnets[0].ipAddressPoolRanges[0].start $requirements.nsxOverlay.rangeStart 'NSX TEP range start is wrong'
Assert-Equal $sddcSpec.nsxtSpec.ipAddressPoolSpec.subnets[0].ipAddressPoolRanges[0].end $requirements.nsxOverlay.rangeEnd 'NSX TEP range end is wrong'
Assert-Equal $sddcSpec.nsxtSpec.useExistingDeployment $false 'NSX must be a greenfield deployment'
Assert-SecretPlaceholder $sddcSpec.nsxtSpec.rootNsxtManagerPassword 'NSX root password must be a clearly named non-secret placeholder'
Assert-SecretPlaceholder $sddcSpec.nsxtSpec.nsxtAdminPassword 'NSX administrator password must be a clearly named non-secret placeholder'
Assert-SecretPlaceholder $sddcSpec.nsxtSpec.nsxtAuditPassword 'NSX audit password must be a clearly named non-secret placeholder'

Assert-Equal $sddcSpec.datastoreSpec.vsanSpec.failuresToTolerate $requirements.availability.hostFailuresToTolerate 'vSAN failures-to-tolerate is wrong'
Assert-Equal $sddcSpec.datastoreSpec.vsanSpec.esaConfig.enabled $true 'vSAN ESA must be enabled'

Assert-Equal $sddcSpec.vcfOperationsFleetManagementSpec.hostname $requirements.appliances.operationsFleetManager 'Fleet Management FQDN is wrong'
Assert-Equal $sddcSpec.vcfOperationsFleetManagementSpec.useExistingDeployment $false 'Fleet Management must be a greenfield deployment'
Assert-SecretPlaceholder $sddcSpec.vcfOperationsFleetManagementSpec.rootUserPassword 'Fleet Management root password must be a clearly named non-secret placeholder'
Assert-SecretPlaceholder $sddcSpec.vcfOperationsFleetManagementSpec.adminUserPassword 'Fleet Management administrator password must be a clearly named non-secret placeholder'
Assert-SetEqual @($sddcSpec.vcfOperationsSpec.nodes.hostname) @($requirements.appliances.operationsNodes) 'VCF Operations node topology is wrong'
Assert-True (@($sddcSpec.vcfOperationsSpec.nodes).Count -ge [int]$requirements.availability.minimumOperationsNodes) 'VCF Operations topology is not highly available'
Assert-SetEqual @($sddcSpec.vcfOperationsSpec.nodes.type) @('master', 'replica', 'data') 'VCF Operations nodes must provide master, replica, and data roles'
Assert-Equal $sddcSpec.vcfOperationsSpec.loadBalancerFqdn $requirements.appliances.operationsLoadBalancer 'VCF Operations load balancer is wrong'
Assert-Equal $sddcSpec.vcfOperationsSpec.useExistingDeployment $false 'VCF Operations must be a greenfield deployment'
Assert-SecretPlaceholder $sddcSpec.vcfOperationsSpec.adminUserPassword 'VCF Operations administrator password must be a clearly named non-secret placeholder'
foreach ($operationsNode in $sddcSpec.vcfOperationsSpec.nodes) {
    Assert-SecretPlaceholder $operationsNode.rootUserPassword "VCF Operations node $($operationsNode.hostname) root password must be a clearly named non-secret placeholder"
}
Assert-Equal $sddcSpec.vcfAutomationSpec.hostname $requirements.appliances.automation 'VCF Automation FQDN is wrong'
Assert-Equal $sddcSpec.vcfAutomationSpec.nodePrefix $requirements.appliances.automationNodePrefix 'VCF Automation node prefix is wrong'
Assert-Equal $sddcSpec.vcfAutomationSpec.internalClusterCidr $requirements.appliances.automationInternalClusterCidr 'VCF Automation internal CIDR is wrong'
Assert-SetEqual @($sddcSpec.vcfAutomationSpec.ipPool) @($requirements.appliances.automationIpPool) 'VCF Automation IP pool is wrong'
Assert-True (@($sddcSpec.vcfAutomationSpec.ipPool).Count -eq [int]$requirements.availability.requiredAutomationIpCount) 'VCF Automation is not sized for the required HA model'
Assert-Equal $sddcSpec.vcfAutomationSpec.useExistingDeployment $false 'VCF Automation must be a greenfield deployment'
Assert-SecretPlaceholder $sddcSpec.vcfAutomationSpec.adminUserPassword 'VCF Automation administrator password must be a clearly named non-secret placeholder'

$migrationPlanPath = Join-Path $RepositoryRoot 'output/migration-plan.json'
if (-not (Test-Path -LiteralPath $migrationPlanPath -PathType Leaf)) {
    Stop-Verification 'output/migration-plan.json is missing'
}
$migrationPlanJson = Get-Content -LiteralPath $migrationPlanPath -Raw
try {
    $migrationSchemaValid = Test-Json -Json $migrationPlanJson -SchemaFile (Join-Path $RepositoryRoot 'schemas/migration-plan.schema.json') -ErrorAction Stop
}
catch {
    Stop-Verification "output/migration-plan.json does not validate against schemas/migration-plan.schema.json: $($_.Exception.Message)"
}
if (-not $migrationSchemaValid) {
    Stop-Verification 'output/migration-plan.json does not validate against schemas/migration-plan.schema.json'
}
$migrationPlan = $migrationPlanJson | ConvertFrom-Json -Depth 100

Assert-Equal $migrationPlan.estateId $inventory.estateId 'migration plan estate ID is wrong'
Assert-Equal $migrationPlan.targetVcfVersion $compatibility.targetVcfVersion 'migration target VCF version is wrong'
Assert-True (@($migrationPlan.steps).Count -eq @($inventory.components).Count) 'migration plan must contain exactly one step for every inventory component'
Assert-SetEqual @($migrationPlan.steps.componentId) @($inventory.components.id) 'migration plan component coverage is wrong'
Assert-True (@($migrationPlan.steps.componentId | Sort-Object -Unique).Count -eq @($migrationPlan.steps).Count) 'migration plan contains duplicate components'

$stepById = @{}
for ($index = 0; $index -lt @($migrationPlan.steps).Count; $index++) {
    $step = $migrationPlan.steps[$index]
    Assert-Equal $step.order ($index + 1) 'migration step order values must be contiguous and match array order'
    $stepById[$step.componentId] = $step
}

foreach ($component in $inventory.components) {
    $rule = @($compatibility.rules | Where-Object { $_.componentId -eq $component.id })
    Assert-True ($rule.Count -eq 1) "pinned compatibility rule missing or ambiguous for $($component.id)"
    $step = $stepById[$component.id]
    Assert-Equal $step.componentName $component.name "component name is wrong for $($component.id)"
    Assert-Equal $step.currentVersion $component.version "source version is wrong for $($component.id)"
    Assert-Equal $step.targetProduct $rule[0].targetProduct "target product is wrong for $($component.id)"
    Assert-Equal $step.targetVersion $rule[0].targetVersion "target version is wrong for $($component.id)"
    Assert-Equal $step.disposition $rule[0].disposition "disposition is wrong for $($component.id)"
    Assert-Equal $step.order $rule[0].sequence "pinned sequence is wrong for $($component.id)"
    Assert-SetEqual @($step.gates) @($rule[0].requiredGates) "technical gates are wrong for $($component.id)"
    if ($rule[0].PSObject.Properties.Name -contains 'supportBoundary') {
        Assert-Equal $step.supportBoundary $rule[0].supportBoundary "support boundary is missing or wrong for $($component.id)"
    }
}

foreach ($constraint in $compatibility.orderingConstraints) {
    Assert-True ($stepById[$constraint.before].order -lt $stepById[$constraint.after].order) "ordering constraint $($constraint.before) before $($constraint.after) is violated"
}

# The research log is checked as an artifact. Verification remains hermetic:
# live reachability belongs to the implementation-time research requested by
# the task, while the checked URLs must identify HTTPS Broadcom sources.
$researchSourcesPath = Join-Path $RepositoryRoot 'output/research-sources.json'
if (-not (Test-Path -LiteralPath $researchSourcesPath -PathType Leaf)) {
    Stop-Verification 'output/research-sources.json is missing'
}
try {
    $research = Get-Content -LiteralPath $researchSourcesPath -Raw | ConvertFrom-Json -Depth 100 -ErrorAction Stop
}
catch {
    Stop-Verification "output/research-sources.json is not valid JSON: $($_.Exception.Message)"
}
Assert-True (@($research.sources).Count -ge 2) 'research log must contain multiple live Broadcom sources'
$seenResearchUrls = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($source in $research.sources) {
    Assert-NonEmptyString $source.title 'every research source must have a non-empty title'
    Assert-NonEmptyString $source.url "research source '$($source.title)' must have a URL"
    $sourceUri = $null
    if (-not [uri]::TryCreate([string]$source.url, [UriKind]::Absolute, [ref]$sourceUri)) {
        Stop-Verification "research source '$($source.title)' does not have an absolute URL"
    }
    Assert-Equal $sourceUri.Scheme 'https' "research source '$($source.title)' must use HTTPS"
    Assert-True ($sourceUri.Host -eq 'broadcom.com' -or $sourceUri.Host.EndsWith('.broadcom.com', [StringComparison]::OrdinalIgnoreCase)) "research source '$($source.title)' must be Broadcom-published"
    Assert-True $seenResearchUrls.Add($sourceUri.AbsoluteUri) "research source URL is duplicated: $($source.url)"

    if ($source.consultedAt -is [DateTime]) {
        Assert-Equal $source.consultedAt.Kind ([DateTimeKind]::Utc) "research source '$($source.title)' consultation time must be UTC"
    }
    else {
        Assert-NonEmptyString $source.consultedAt "research source '$($source.title)' must record a UTC consultation time"
        $consultedAt = [DateTimeOffset]::MinValue
        if (-not [DateTimeOffset]::TryParseExact(
            [string]$source.consultedAt,
            'yyyy-MM-ddTHH:mm:ssZ',
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::AssumeUniversal,
            [ref]$consultedAt
        )) {
            Stop-Verification "research source '$($source.title)' consultation time must be UTC in yyyy-MM-ddTHH:mm:ssZ format"
        }
    }
    Assert-True (@($source.factsUsed).Count -ge 1) "research source '$($source.title)' must record at least one fact used"
    foreach ($fact in $source.factsUsed) {
        Assert-NonEmptyString $fact "research source '$($source.title)' contains an empty fact"
    }
}

# Module contract checks happen last and use the PowerShell parser rather than
# importing the environment-provided VMware dependency during verification.
$moduleManifestPath = Join-Path $RepositoryRoot 'src/Vcf.GreenfieldArchitecture/Vcf.GreenfieldArchitecture.psd1'
$moduleSourcePath = Join-Path $RepositoryRoot 'src/Vcf.GreenfieldArchitecture/Vcf.GreenfieldArchitecture.psm1'
Assert-True (Test-Path -LiteralPath $moduleManifestPath -PathType Leaf) 'PowerShell module manifest is missing'
Assert-True (Test-Path -LiteralPath $moduleSourcePath -PathType Leaf) 'PowerShell module implementation is missing'
$moduleManifest = Import-PowerShellDataFile -LiteralPath $moduleManifestPath
$requiredModuleNames = @($moduleManifest.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ } elseif ($_ -is [hashtable]) { $_.ModuleName } else { $_.ModuleName }
})
Assert-True ($requiredModuleNames -contains 'VMware.Sdk.Vcf.Installer') 'module manifest must require VMware.Sdk.Vcf.Installer'
Assert-True (@($moduleManifest.FunctionsToExport) -contains 'New-VcfGreenfieldArchitecture') 'module manifest must export New-VcfGreenfieldArchitecture'

$tokens = $null
$parseErrors = $null
$moduleAst = [Management.Automation.Language.Parser]::ParseFile($moduleSourcePath, [ref]$tokens, [ref]$parseErrors)
Assert-True (@($parseErrors).Count -eq 0) 'PowerShell module must parse without syntax errors'
$architectureFunctions = @($moduleAst.FindAll({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'New-VcfGreenfieldArchitecture'
}, $true))
Assert-True ($architectureFunctions.Count -eq 1) 'module must implement New-VcfGreenfieldArchitecture exactly once'
$architectureFunction = $architectureFunctions[0]
$parameterNames = @($architectureFunction.Body.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
foreach ($requiredParameter in @('RequirementsPath', 'EstateInventoryPath', 'CompatibilitySnapshotPath', 'OutputDirectory')) {
    Assert-True ($parameterNames -contains $requiredParameter) "New-VcfGreenfieldArchitecture is missing parameter $requiredParameter"
}
$commandAsts = @($architectureFunction.Body.FindAll({
    param($node)
    $node -is [Management.Automation.Language.CommandAst]
}, $true))
$commandNames = @($commandAsts | ForEach-Object { $_.GetCommandName() } | Where-Object { $null -ne $_ })
$requiredInstallerConstructors = @(
    'Initialize-VcfInstallerSddcHostSpec',
    'Initialize-VcfInstallerSddcNetworkSpec',
    'Initialize-VcfInstallerDvsSpec',
    'Initialize-VcfInstallerSddcVcenterSpec',
    'Initialize-VcfInstallerSddcClusterSpec',
    'Initialize-VcfInstallerDnsSpec',
    'Initialize-VcfInstallerSddcManagerSpec',
    'Initialize-VcfInstallerNsxtManagerSpec',
    'Initialize-VcfInstallerSddcNsxtSpec',
    'Initialize-VcfInstallerVsanSpec',
    'Initialize-VcfInstallerSddcDatastoreSpec',
    'Initialize-VcfInstallerVcfOperationsFleetManagementSpec',
    'Initialize-VcfInstallerVcfOperationsNode',
    'Initialize-VcfInstallerVcfOperationsSpec',
    'Initialize-VcfInstallerVcfAutomationSpec',
    'Initialize-VcfInstallerSddcSpec'
)
foreach ($constructor in $requiredInstallerConstructors) {
    Assert-True ($commandNames -contains $constructor) "module must construct architecture objects with $constructor"
}
Assert-True ($commandNames -contains 'ConvertTo-Json') 'module must serialize its generated artifacts as JSON'

$vendoredBinaries = @(Get-ChildItem -LiteralPath (Join-Path $RepositoryRoot 'src') -Recurse -File | Where-Object {
    $_.Extension -in @('.dll', '.nupkg', '.cat')
})
Assert-True ($vendoredBinaries.Count -eq 0) 'VMware modules or binaries must not be vendored beneath src'

Write-Output 'VERIFICATION PASSED: installer schema, architecture requirements, pinned migration compatibility, research log, and PowerShell module checks succeeded.'
