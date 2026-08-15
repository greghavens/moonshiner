$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
$artifactPath = Join-Path $root 'installer-spec.json'
$schemaPath = Join-Path $root 'spec/vcf-migration-installer-spec.schema.json'

function Fail {
    param([Parameter(Mandatory)][string] $Message)
    Write-Error $Message
    exit 1
}

function Assert-True {
    param(
        [Parameter(Mandatory)][bool] $Condition,
        [Parameter(Mandatory)][string] $Message
    )
    if (-not $Condition) {
        Fail $Message
    }
}

# The submitted artifact is validated against its declared installer-spec schema
# before fixtures, module code, or semantic expectations are inspected.
if (-not (Test-Path -LiteralPath $artifactPath -PathType Leaf)) {
    Fail 'Schema validation failed: installer-spec.json is missing.'
}

try {
    $artifactRaw = Get-Content -LiteralPath $artifactPath -Raw
    $schemaValid = Test-Json -Json $artifactRaw -SchemaFile $schemaPath -ErrorAction Stop
}
catch {
    Fail "Schema validation failed: $($_.Exception.Message)"
}

if (-not $schemaValid) {
    Fail 'Schema validation failed: installer-spec.json does not conform.'
}

Write-Output 'Schema validation passed.'

$inventoryPath = Join-Path $root 'fixtures/estate-inventory.json'
$compatibilityPath = Join-Path $root 'fixtures/compatibility-snapshot.json'
$researchPath = Join-Path $root 'research-sources.json'
$manifestPath = Join-Path $root 'VcfMigrationArchitecture/VcfMigrationArchitecture.psd1'
$modulePath = Join-Path $root 'VcfMigrationArchitecture/VcfMigrationArchitecture.psm1'

foreach ($requiredPath in @($inventoryPath, $compatibilityPath, $researchPath, $manifestPath, $modulePath)) {
    Assert-True (Test-Path -LiteralPath $requiredPath -PathType Leaf) "Required deliverable is missing: $requiredPath"
}

try {
    $artifact = $artifactRaw | ConvertFrom-Json -Depth 100
    $inventory = Get-Content -LiteralPath $inventoryPath -Raw | ConvertFrom-Json -Depth 100
    $compatibility = Get-Content -LiteralPath $compatibilityPath -Raw | ConvertFrom-Json -Depth 100
    $researchSources = @(Get-Content -LiteralPath $researchPath -Raw | ConvertFrom-Json -Depth 20)
}
catch {
    Fail "A JSON input could not be parsed: $($_.Exception.Message)"
}

function Assert-StringSequence {
    param(
        [AllowEmptyCollection()][object[]] $Actual,
        [AllowEmptyCollection()][object[]] $Expected,
        [Parameter(Mandatory)][string] $Label
    )
    $actualStrings = @($Actual | ForEach-Object { [string] $_ })
    $expectedStrings = @($Expected | ForEach-Object { [string] $_ })
    Assert-True ($actualStrings.Count -eq $expectedStrings.Count) "$Label count differs: expected $($expectedStrings.Count), got $($actualStrings.Count)."
    for ($index = 0; $index -lt $expectedStrings.Count; $index++) {
        Assert-True ($actualStrings[$index] -ceq $expectedStrings[$index]) "$Label differs at index $($index): expected '$($expectedStrings[$index])', got '$($actualStrings[$index])'."
    }
}

function Assert-JsonEqual {
    param(
        [Parameter(Mandatory)] $Actual,
        [Parameter(Mandatory)] $Expected,
        [Parameter(Mandatory)][string] $Label
    )
    $actualJson = $Actual | ConvertTo-Json -Depth 100 -Compress
    $expectedJson = $Expected | ConvertTo-Json -Depth 100 -Compress
    Assert-True ($actualJson -ceq $expectedJson) "$Label differs from the pinned fixture."
}

# Research is graded as a submitted artifact without making acceptance depend on
# the network. Every entry must be a dated HTTPS Broadcom publication and the
# source catalog must cover all four subjects requested by the task.
Assert-True ($researchSources.Count -gt 0) 'research-sources.json must contain at least one source.'
$researchSubjects = @('migration-path', 'content-compatibility', 'support-boundary', 'storage-network')
$coveredSubjects = @{}
$seenResearchUrls = @{}
foreach ($source in $researchSources) {
    foreach ($propertyName in @('title', 'publisher', 'url', 'accessedOn', 'supports')) {
        Assert-True ($source.PSObject.Properties.Name -ccontains $propertyName) "Research source is missing $propertyName."
    }
    Assert-True (-not [string]::IsNullOrWhiteSpace([string] $source.title)) 'Research source title must not be empty.'
    Assert-True (-not [string]::IsNullOrWhiteSpace([string] $source.publisher)) 'Research source publisher must not be empty.'

    $uri = $null
    $isAbsoluteUri = [Uri]::TryCreate([string] $source.url, [UriKind]::Absolute, [ref] $uri)
    Assert-True ($isAbsoluteUri -and $uri.Scheme -ceq 'https') "Research source URL must be absolute HTTPS: $($source.url)"
    Assert-True ([string]::IsNullOrEmpty($uri.UserInfo)) "Research source URL must not contain credentials: $($source.url)"
    $researchHost = $uri.DnsSafeHost.ToLowerInvariant()
    $isBroadcomPublication = $researchHost -eq 'broadcom.com' -or $researchHost.EndsWith('.broadcom.com') -or $researchHost -eq 'vmware.com' -or $researchHost.EndsWith('.vmware.com')
    Assert-True $isBroadcomPublication "Research source is not hosted on a Broadcom publication domain: $($source.url)"
    Assert-True (-not $seenResearchUrls.ContainsKey([string] $uri.AbsoluteUri)) "Research source URL is duplicated: $($source.url)"
    $seenResearchUrls[[string] $uri.AbsoluteUri] = $true

    $accessDate = [datetime]::MinValue
    $validAccessDate = [datetime]::TryParseExact(
        [string] $source.accessedOn,
        'yyyy-MM-dd',
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::None,
        [ref] $accessDate
    )
    Assert-True $validAccessDate "Research source accessedOn must be an ISO date: $($source.accessedOn)"

    $sourceSubjects = @($source.supports)
    Assert-True ($sourceSubjects.Count -gt 0) "Research source must support at least one subject: $($source.url)"
    foreach ($subject in $sourceSubjects) {
        Assert-True ($researchSubjects -ccontains [string] $subject) "Research source has an unknown subject '$subject'."
        $coveredSubjects[[string] $subject] = $true
    }
}
foreach ($subject in $researchSubjects) {
    Assert-True ($coveredSubjects.ContainsKey($subject)) "Research sources do not cover required subject: $subject"
}

Assert-True ([string] $artifact.fixture.inventoryId -ceq [string] $inventory.estateId) 'Artifact inventoryId does not identify the supplied estate.'
Assert-True ([string] $artifact.fixture.compatibilitySnapshotId -ceq [string] $compatibility.snapshotId) 'Artifact compatibilitySnapshotId does not identify the pinned snapshot.'
Assert-True ([string] $artifact.estateId -ceq [string] $inventory.estateId) 'Artifact estateId differs from inventory.'
Assert-True ([string] $artifact.target.vcfVersion -ceq [string] $compatibility.targetRelease) 'Target VCF version differs from the pinned release.'
Assert-True ([string] $artifact.target.managementDomain -ceq [string] $inventory.site.managementDomain) 'Target management domain differs from inventory.'
Assert-True ([string] $artifact.target.managementCluster -ceq [string] $inventory.site.managementCluster) 'Target management cluster differs from inventory.'

$sourceProducts = @($inventory.sourceProducts)
$productPaths = @($compatibility.productPaths)

# Support boundaries must identify every source product/version exactly once.
Assert-True (@($artifact.supportBoundaries).Count -eq $sourceProducts.Count) 'Support boundary count must equal source product count.'
foreach ($sourceProduct in $sourceProducts) {
    $path = @($productPaths | Where-Object { $_.sourceId -ceq $sourceProduct.id })
    $boundaries = @($artifact.supportBoundaries | Where-Object { $_.sourceId -ceq $sourceProduct.id })
    Assert-True ($path.Count -eq 1) "Pinned product path missing or duplicated for $($sourceProduct.id)."
    Assert-True ($boundaries.Count -eq 1) "Support boundary missing or duplicated for $($sourceProduct.id)."
    $boundary = $boundaries[0]
    Assert-True ([string] $boundary.product -ceq [string] $sourceProduct.product) "Support boundary product mismatch for $($sourceProduct.id)."
    Assert-True ([string] $boundary.version -ceq [string] $sourceProduct.version) "Support boundary version mismatch for $($sourceProduct.id)."
    Assert-True ([string] $boundary.kind -ceq [string] $path[0].supportBoundary.kind) "Support boundary kind mismatch for $($sourceProduct.id)."
    Assert-True ([string] $boundary.date -ceq [string] $path[0].supportBoundary.date) "Support boundary date mismatch for $($sourceProduct.id)."
}

# The storage decision compares both fixed options and reflects their distinct
# host counts and physical vSAN network requirements.
$decision = $artifact.storageDecision
Assert-True ([string] $decision.selectedOption -ceq [string] $compatibility.requiredDecision.selectedOption) 'Selected storage architecture differs from the pinned decision.'
Assert-True ([string] $decision.rationaleCode -ceq [string] $compatibility.requiredDecision.rationaleCode) 'Storage rationale code differs from the pinned decision.'
Assert-True (@($decision.options).Count -eq 2) 'Exactly OSA and ESA must be compared.'

foreach ($expectedOption in @($compatibility.requiredDecision.storageOptions)) {
    $actualOptions = @($decision.options | Where-Object { $_.option -ceq $expectedOption.option })
    $pools = @($inventory.hostPools | Where-Object { $_.option -ceq $expectedOption.option })
    Assert-True ($actualOptions.Count -eq 1) "Storage option missing or duplicated: $($expectedOption.option)."
    Assert-True ($pools.Count -eq 1) "Inventory host pool missing or duplicated: $($expectedOption.option)."
    $actualOption = $actualOptions[0]
    $pool = $pools[0]
    foreach ($property in @('architecture', 'requiredHostCount', 'minimumVsanNetworkGbps', 'requiredMtu', 'readyNodeProfile', 'usesExistingLeafFabric')) {
        Assert-True ([string] $actualOption.$property -ceq [string] $expectedOption.$property) "$($expectedOption.option) $property differs from the pinned snapshot."
    }
    Assert-True ([string] $actualOption.poolId -ceq [string] $pool.poolId) "$($expectedOption.option) poolId differs from inventory."
    Assert-True (@($actualOption.selectedHostnames).Count -eq [int] $expectedOption.requiredHostCount) "$($expectedOption.option) selected host count is inconsistent."
    foreach ($selectedHostname in @($actualOption.selectedHostnames)) {
        Assert-True (@($pool.availableHosts) -ccontains [string] $selectedHostname) "$($expectedOption.option) selected an unknown host: $selectedHostname"
    }
    Assert-StringSequence @($actualOption.selectedHostnames | Sort-Object) @($actualOption.selectedHostnames | Sort-Object -Unique) "$($expectedOption.option) selected-host uniqueness"
}
Assert-JsonEqual @($decision.networkChanges) @($compatibility.requiredDecision.selectedNetworkChanges) 'Selected storage network changes'

# Placement and sizing are derived from the target profiles, not accepted as a
# detached narrative.
$expectedProfiles = @($inventory.target.sizingProfiles)
Assert-True (@($artifact.componentPlacements).Count -eq $expectedProfiles.Count) 'Every target sizing profile must have exactly one placement.'
$expectedNodeCount = 0
$expectedVcpu = 0
$expectedMemory = 0
$expectedDisk = 0
foreach ($profile in $expectedProfiles) {
    $placements = @($artifact.componentPlacements | Where-Object { $_.component -ceq $profile.component })
    Assert-True ($placements.Count -eq 1) "Placement missing or duplicated for $($profile.component)."
    $placement = $placements[0]
    Assert-True ([string] $placement.version -ceq [string] $compatibility.targetRelease) "Placement version mismatch for $($profile.component)."
    Assert-True ([string] $placement.managementDomain -ceq [string] $inventory.site.managementDomain) "Placement domain mismatch for $($profile.component)."
    Assert-True ([string] $placement.cluster -ceq [string] $inventory.site.managementCluster) "Placement cluster mismatch for $($profile.component)."
    Assert-True ([string] $placement.resourcePool -ceq [string] $inventory.target.resourcePool) "Resource pool mismatch for $($profile.component)."
    Assert-True ([string] $placement.storagePolicy -ceq [string] $inventory.target.storagePolicy) "Storage policy mismatch for $($profile.component)."
    Assert-True ([int] $placement.nodeCount -eq [int] $profile.nodeCount) "Node count mismatch for $($profile.component)."
    Assert-True ([int] $placement.nodeShape.vCpu -eq [int] $profile.vCpuPerNode) "vCPU sizing mismatch for $($profile.component)."
    Assert-True ([int] $placement.nodeShape.memoryGiB -eq [int] $profile.memoryGiBPerNode) "Memory sizing mismatch for $($profile.component)."
    Assert-True ([int] $placement.nodeShape.diskGiB -eq [int] $profile.diskGiBPerNode) "Disk sizing mismatch for $($profile.component)."
    Assert-True ([bool] $placement.antiAffinity -eq [bool] $profile.antiAffinity) "Anti-affinity mismatch for $($profile.component)."
    Assert-True ([string] $placement.network.segment -ceq [string] $inventory.target.networkSegment) "Network segment mismatch for $($profile.component)."
    Assert-True ([int] $placement.network.vlan -eq [int] $inventory.target.networkVlan) "Network VLAN mismatch for $($profile.component)."
    Assert-True ([string] $placement.network.serviceAddress -ceq [string] $profile.serviceAddress) "Service address mismatch for $($profile.component)."
    $expectedNodeCount += [int] $profile.nodeCount
    $expectedVcpu += [int] $profile.nodeCount * [int] $profile.vCpuPerNode
    $expectedMemory += [int] $profile.nodeCount * [int] $profile.memoryGiBPerNode
    $expectedDisk += [int] $profile.nodeCount * [int] $profile.diskGiBPerNode
}
$selectedStorage = @($compatibility.requiredDecision.storageOptions | Where-Object { $_.option -ceq $compatibility.requiredDecision.selectedOption })[0]
Assert-True ([int] $artifact.resourceSummary.managementNodeCount -eq $expectedNodeCount) 'Resource summary node count is incorrect.'
Assert-True ([int] $artifact.resourceSummary.totalVcpu -eq $expectedVcpu) 'Resource summary vCPU is incorrect.'
Assert-True ([int] $artifact.resourceSummary.totalMemoryGiB -eq $expectedMemory) 'Resource summary memory is incorrect.'
Assert-True ([int] $artifact.resourceSummary.totalDiskGiB -eq $expectedDisk) 'Resource summary disk is incorrect.'
Assert-True ([int] $artifact.resourceSummary.selectedHostCount -eq [int] $selectedStorage.requiredHostCount) 'Resource summary selected host count is incorrect.'

# Every product transition and every content item is checked against the pinned
# compatibility rules. Item IDs must be neither omitted nor counted twice.
$allInventoryItems = @($sourceProducts | ForEach-Object { @($_.content) } | ForEach-Object { $_.id })
$allArtifactItems = [System.Collections.Generic.List[string]]::new()
Assert-True (@($artifact.transitions).Count -eq $sourceProducts.Count) 'Transition count must equal source product count.'
foreach ($sourceProduct in $sourceProducts) {
    $transitions = @($artifact.transitions | Where-Object { $_.sourceId -ceq $sourceProduct.id })
    $path = @($productPaths | Where-Object { $_.sourceId -ceq $sourceProduct.id })[0]
    Assert-True ($transitions.Count -eq 1) "Transition missing or duplicated for $($sourceProduct.id)."
    $transition = $transitions[0]
    foreach ($property in @('sourceProduct', 'sourceVersion', 'targetComponent', 'targetVersion', 'migrationMode')) {
        $expectedValue = if ($property -eq 'sourceProduct') { $sourceProduct.product } elseif ($property -eq 'sourceVersion') { $sourceProduct.version } else { $path.$property }
        Assert-True ([string] $transition.$property -ceq [string] $expectedValue) "Transition $property mismatch for $($sourceProduct.id)."
    }
    Assert-StringSequence @($transition.stepRefs) @($path.requiredActions) "Transition stepRefs for $($sourceProduct.id)"
    $transitionItems = @($transition.carryForward) + @($transition.abandoned)
    Assert-True ($transitionItems.Count -eq @($sourceProduct.content).Count) "Content item count differs for $($sourceProduct.id)."
    foreach ($contentItem in @($sourceProduct.content)) {
        $rule = @($compatibility.contentRules | Where-Object { $_.itemId -ceq $contentItem.id })
        Assert-True ($rule.Count -eq 1) "Pinned content rule missing or duplicated for $($contentItem.id)."
        $carried = @($transition.carryForward | Where-Object { $_.itemId -ceq $contentItem.id })
        $abandoned = @($transition.abandoned | Where-Object { $_.itemId -ceq $contentItem.id })
        Assert-True (($carried.Count + $abandoned.Count) -eq 1) "Content item must be accounted exactly once: $($contentItem.id)."
        if ($rule[0].disposition -ceq 'carry') {
            Assert-True ($carried.Count -eq 1) "Content item must carry forward: $($contentItem.id)."
            Assert-True ([string] $carried[0].method -ceq [string] $rule[0].method) "Carry method mismatch for $($contentItem.id)."
            Assert-True ([string] $carried[0].verification -ceq [string] $rule[0].verification) "Carry verification mismatch for $($contentItem.id)."
        }
        else {
            Assert-True ($abandoned.Count -eq 1) "Content item must be abandoned: $($contentItem.id)."
            Assert-True ([string] $abandoned[0].reason -ceq [string] $rule[0].reason) "Abandonment reason mismatch for $($contentItem.id)."
            Assert-True ([string] $abandoned[0].finalDisposition -ceq [string] $rule[0].finalDisposition) "Final disposition mismatch for $($contentItem.id)."
        }
        $allArtifactItems.Add([string] $contentItem.id)
    }
}
Assert-StringSequence @($allArtifactItems | Sort-Object) @($allInventoryItems | Sort-Object) 'Global content inventory coverage'

# Gates are fixed technical evidence gates, and every ordered step uses its
# pinned dependencies and gates.
Assert-True (@($artifact.gates).Count -eq @($compatibility.requiredGates).Count) 'Gate catalog count differs from the pinned snapshot.'
foreach ($expectedGate in @($compatibility.requiredGates)) {
    $gates = @($artifact.gates | Where-Object { $_.id -ceq $expectedGate.id })
    Assert-True ($gates.Count -eq 1) "Gate missing or duplicated: $($expectedGate.id)."
    Assert-True ([string] $gates[0].evidence -ceq [string] $expectedGate.evidence) "Gate evidence mismatch: $($expectedGate.id)."
}

$expectedSteps = @($compatibility.requiredSteps)
$actualSteps = @($artifact.migrationPlan)
Assert-True ($actualSteps.Count -eq $expectedSteps.Count) 'Migration plan step count differs from the pinned snapshot.'
Assert-StringSequence @($actualSteps.id) @($expectedSteps.id) 'Migration plan order'
$gateIds = @($artifact.gates.id)
$seenStepIds = @{}
$usedGateIds = @{}
$planAffectedProducts = @{}
$previousSequence = 0
for ($index = 0; $index -lt $actualSteps.Count; $index++) {
    $step = $actualSteps[$index]
    $expectedStep = $expectedSteps[$index]
    Assert-True ([int] $step.sequence -gt $previousSequence) "Step $($step.id) sequence must be strictly increasing."
    $previousSequence = [int] $step.sequence
    Assert-StringSequence @($step.dependsOn) @($expectedStep.dependsOn) "Dependencies for step $($step.id)"
    Assert-StringSequence @($step.gateIds) @($expectedStep.gateIds) "Gates for step $($step.id)"
    foreach ($dependency in @($step.dependsOn)) {
        Assert-True ($seenStepIds.ContainsKey([string] $dependency)) "Step $($step.id) depends on a step that is not earlier: $dependency."
    }
    foreach ($gateId in @($step.gateIds)) {
        Assert-True ($gateIds -ccontains [string] $gateId) "Step $($step.id) references an undefined gate: $gateId."
        $usedGateIds[[string] $gateId] = $true
    }
    foreach ($sourceId in @($step.affectedProducts)) {
        Assert-True (@($sourceProducts.id) -ccontains [string] $sourceId) "Step $($step.id) references an unknown affected product: $sourceId."
        $planAffectedProducts[[string] $sourceId] = $true
    }
    $seenStepIds[[string] $step.id] = $true
}
foreach ($gateId in $gateIds) {
    Assert-True ($usedGateIds.ContainsKey([string] $gateId)) "Gate is never used by a migration step: $gateId."
}
foreach ($sourceId in @($sourceProducts.id)) {
    Assert-True ($planAffectedProducts.ContainsKey([string] $sourceId)) "Inventoried source product is never named by the migration plan: $sourceId."
}

# The architecture must map the three VCF Installer lifecycle operations named
# by the task. Purpose wording may vary, and additional discovered bindings are
# allowed.
$requiredBindingKeys = @(
    'POST /v1/sddcs/validations',
    'POST /v1/sddcs/resources-calculation',
    'POST /v1/sddcs'
)
$actualBindingKeys = @($artifact.vcfInstallerBindings | ForEach-Object { "$($_.method) $($_.path)" })
Assert-StringSequence @($actualBindingKeys | Sort-Object) @($actualBindingKeys | Sort-Object -Unique) 'VCF Installer binding uniqueness'
foreach ($bindingKey in $requiredBindingKeys) {
    Assert-True ($actualBindingKeys -ccontains $bindingKey) "Required VCF Installer binding is missing: $bindingKey"
}

# Confirm a real PowerShell module implementation and the harness-provisioned
# VMware SDK dependency, then regenerate the artifact offline.
try {
    $manifest = Import-PowerShellDataFile -LiteralPath $manifestPath
}
catch {
    Fail "PowerShell module manifest is invalid: $($_.Exception.Message)"
}
Assert-True ([string] $manifest.RootModule -ceq 'VcfMigrationArchitecture.psm1') 'Manifest RootModule must be VcfMigrationArchitecture.psm1.'
Assert-True (@($manifest.FunctionsToExport) -ccontains 'New-VcfMigrationInstallerSpec') 'Manifest must export New-VcfMigrationInstallerSpec.'
$sdkRequirements = @($manifest.RequiredModules | Where-Object {
    ($_ -is [string] -and $_ -ceq 'VMware.Sdk.Vcf.Installer') -or
    ($_ -isnot [string] -and [string] $_.ModuleName -ceq 'VMware.Sdk.Vcf.Installer')
})
Assert-True ($sdkRequirements.Count -eq 1) 'Manifest must declare VMware.Sdk.Vcf.Installer exactly once.'

$vendoredSdkFiles = @(Get-ChildItem -LiteralPath (Split-Path -Parent $manifestPath) -Recurse -File | Where-Object {
    $_.Name.StartsWith('VMware.', [StringComparison]::OrdinalIgnoreCase) -and
    @('.dll', '.psd1', '.psm1') -contains $_.Extension.ToLowerInvariant()
})
Assert-True ($vendoredSdkFiles.Count -eq 0) 'Do not vendor VMware SDK modules or assemblies.'

$tokens = $null
$parseErrors = $null
$moduleAst = [System.Management.Automation.Language.Parser]::ParseFile($modulePath, [ref] $tokens, [ref] $parseErrors)
$parseErrorMessage = @($parseErrors | ForEach-Object { $_.Message }) -join '; '
Assert-True (@($parseErrors).Count -eq 0) "PowerShell module has parse errors: $parseErrorMessage"
$operationDiscoveryCalls = @($moduleAst.FindAll({
    param($node)
    if ($node -isnot [System.Management.Automation.Language.CommandAst]) {
        return $false
    }
    $commandName = $node.GetCommandName()
    $null -ne $commandName -and $commandName.Split('\\')[-1] -ieq 'Get-VcfInstallerOperation'
}, $true))
Assert-True ($operationDiscoveryCalls.Count -gt 0) 'Module implementation must invoke VCF Installer operation discovery.'

$generatedPath = Join-Path $root ".vcfarch-verifier-$PID.json"
$repeatPath = Join-Path $root ".vcfarch-verifier-repeat-$PID.json"
$variantRoot = Join-Path $root ".vcfarch-verifier-variant-$PID"
try {
    Import-Module -Name $manifestPath -Force -ErrorAction Stop
    $operationDiscoveryCommand = Get-Command -Name Get-VcfInstallerOperation -CommandType Cmdlet -ErrorAction Stop
    foreach ($binding in @($artifact.vcfInstallerBindings)) {
        $null = & $operationDiscoveryCommand -Path ([string] $binding.path) -Method ([string] $binding.method) -ErrorAction Stop
    }
    $command = Get-Command -Name New-VcfMigrationInstallerSpec -CommandType Function -ErrorAction Stop
    foreach ($parameterName in @('InventoryPath', 'CompatibilityPath', 'OutputPath')) {
        Assert-True ($command.Parameters.ContainsKey($parameterName)) "New-VcfMigrationInstallerSpec is missing -$parameterName."
    }
    $null = New-VcfMigrationInstallerSpec -InventoryPath $inventoryPath -CompatibilityPath $compatibilityPath -OutputPath $generatedPath
    Assert-True (Test-Path -LiteralPath $generatedPath -PathType Leaf) 'Module did not create its requested output artifact.'
    $generatedRaw = Get-Content -LiteralPath $generatedPath -Raw
    Assert-True (Test-Json -Json $generatedRaw -SchemaFile $schemaPath -ErrorAction Stop) 'Module-generated artifact does not validate against the installer schema.'
    $generated = $generatedRaw | ConvertFrom-Json -Depth 100
    Assert-JsonEqual $generated $artifact 'Checked-in and regenerated installer specifications'

    # The same inputs must produce byte-for-byte stable JSON regardless of the
    # requested output path.
    $null = New-VcfMigrationInstallerSpec -InventoryPath $inventoryPath -CompatibilityPath $compatibilityPath -OutputPath $repeatPath
    $repeatRaw = Get-Content -LiteralPath $repeatPath -Raw
    Assert-True ($repeatRaw -ceq $generatedRaw) 'Module output is not deterministic for identical inputs.'

    # Exercise both input parameters with protected variants. This rejects an
    # implementation that merely copies or returns the checked-in artifact.
    $null = New-Item -ItemType Directory -Path $variantRoot
    $variantInventory = Get-Content -LiteralPath $inventoryPath -Raw | ConvertFrom-Json -Depth 100
    $variantCompatibility = Get-Content -LiteralPath $compatibilityPath -Raw | ConvertFrom-Json -Depth 100
    $variantInventory.estateId = 'chi-mgmt-variant'
    $variantInventory.site.managementDomain = 'chi01-m-variant'
    $variantInventory.target.resourcePool = 'rp-vcf-management-variant'
    $variantInventory.target.sizingProfiles[0].nodeCount = [int] $variantInventory.target.sizingProfiles[0].nodeCount + 1
    $variantInventory.hostPools[0].availableHosts[0] = 'chi-osa-variant-01'
    $variantCompatibility.snapshotId = 'vcf-9.0.1-migration-variant'
    $variantCompatibility.contentRules[0].method = [string] $variantCompatibility.contentRules[0].method + ' Variant input marker.'
    $variantCompatibility.requiredGates[0].evidence = [string] $variantCompatibility.requiredGates[0].evidence + ' Variant input marker.'

    $variantInventoryPath = Join-Path $variantRoot 'estate-inventory.json'
    $variantCompatibilityPath = Join-Path $variantRoot 'compatibility-snapshot.json'
    $variantOutputPath = Join-Path $variantRoot 'installer-spec.json'
    $variantInventory | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $variantInventoryPath -Encoding utf8NoBOM
    $variantCompatibility | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $variantCompatibilityPath -Encoding utf8NoBOM

    $null = New-VcfMigrationInstallerSpec -InventoryPath $variantInventoryPath -CompatibilityPath $variantCompatibilityPath -OutputPath $variantOutputPath
    Assert-True (Test-Path -LiteralPath $variantOutputPath -PathType Leaf) 'Module did not honor the variant OutputPath.'
    $variantRaw = Get-Content -LiteralPath $variantOutputPath -Raw
    Assert-True (Test-Json -Json $variantRaw -SchemaFile $schemaPath -ErrorAction Stop) 'Variant-input artifact does not validate against the installer schema.'
    $variant = $variantRaw | ConvertFrom-Json -Depth 100

    Assert-True ([string] $variant.estateId -ceq [string] $variantInventory.estateId) 'Variant inventory estateId was not consumed.'
    Assert-True ([string] $variant.fixture.compatibilitySnapshotId -ceq [string] $variantCompatibility.snapshotId) 'Variant compatibility snapshotId was not consumed.'
    Assert-True ([string] $variant.target.managementDomain -ceq [string] $variantInventory.site.managementDomain) 'Variant management domain was not consumed.'
    $variantPlacement = @($variant.componentPlacements | Where-Object { $_.component -ceq $variantInventory.target.sizingProfiles[0].component })[0]
    Assert-True ([int] $variantPlacement.nodeCount -eq [int] $variantInventory.target.sizingProfiles[0].nodeCount) 'Variant sizing profile was not consumed.'
    Assert-True ([string] $variantPlacement.resourcePool -ceq [string] $variantInventory.target.resourcePool) 'Variant resource pool was not consumed.'
    $variantOsa = @($variant.storageDecision.options | Where-Object { $_.option -ceq 'OSA' })[0]
    Assert-True (@($variantOsa.selectedHostnames) -ccontains [string] $variantInventory.hostPools[0].availableHosts[0]) 'Variant host inventory was not consumed.'
    $variantCarried = @($variant.transitions.carryForward | Where-Object { $_.itemId -ceq $variantCompatibility.contentRules[0].itemId })[0]
    Assert-True ([string] $variantCarried.method -ceq [string] $variantCompatibility.contentRules[0].method) 'Variant content compatibility rule was not consumed.'
    $variantGate = @($variant.gates | Where-Object { $_.id -ceq $variantCompatibility.requiredGates[0].id })[0]
    Assert-True ([string] $variantGate.evidence -ceq [string] $variantCompatibility.requiredGates[0].evidence) 'Variant technical gate was not consumed.'

    $variantNodeCount = 0
    foreach ($profile in @($variantInventory.target.sizingProfiles)) {
        $variantNodeCount += [int] $profile.nodeCount
    }
    Assert-True ([int] $variant.resourceSummary.managementNodeCount -eq $variantNodeCount) 'Variant resource summary was not recalculated.'
}
catch {
    Fail "PowerShell module execution failed: $($_.Exception.Message)"
}
finally {
    if (Test-Path -LiteralPath $generatedPath) {
        Remove-Item -LiteralPath $generatedPath -Force
    }
    if (Test-Path -LiteralPath $repeatPath) {
        Remove-Item -LiteralPath $repeatPath -Force
    }
    if (Test-Path -LiteralPath $variantRoot) {
        Remove-Item -LiteralPath $variantRoot -Recurse -Force
    }
}

Write-Output 'All VCF migration architecture checks passed.'
