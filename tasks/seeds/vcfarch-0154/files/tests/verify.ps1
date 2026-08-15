Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$artifactPath = Join-Path $root 'migration-installer-spec.json'
$schemaPath = Join-Path $root 'specs/migration-installer-spec.schema.json'

function Fail([string]$Message) {
    [Console]::Error.WriteLine("FAIL: $Message")
    exit 1
}

# First acceptance contract: do not read the fixture, snapshot, module, or any
# other submission input until the artifact passes its own installer schema.
if (-not (Test-Path -LiteralPath $artifactPath -PathType Leaf)) {
    Fail 'schema validation: migration-installer-spec.json is missing'
}
try {
    $artifactJson = [System.IO.File]::ReadAllText($artifactPath)
    $schemaValid = Test-Json -Json $artifactJson -SchemaFile $schemaPath -ErrorAction Stop
} catch {
    Fail "schema validation: $($_.Exception.Message)"
}
if (-not $schemaValid) {
    Fail 'schema validation: migration-installer-spec.json does not satisfy its installer schema'
}

# Schema validation has passed. Semantic checks are limited to the artifact,
# protected estate fixture, pinned compatibility snapshot, and module sources.
$plan = $artifactJson | ConvertFrom-Json -Depth 100
$inventory = Get-Content -LiteralPath (Join-Path $root 'fixtures/estate-inventory.json') -Raw |
    ConvertFrom-Json -Depth 100
$snapshot = Get-Content -LiteralPath (Join-Path $root 'specs/compatibility-snapshot.json') -Raw |
    ConvertFrom-Json -Depth 100

$researchPath = Join-Path $root 'research-consulted.md'
if (-not (Test-Path -LiteralPath $researchPath -PathType Leaf)) {
    Fail 'research-consulted.md is required'
}
$researchText = [System.IO.File]::ReadAllText($researchPath)
if ($researchText -notmatch '(?i)\baccess(?:ed)?\s*:?[ \t]+[0-9]{4}-[0-9]{2}-[0-9]{2}\b') {
    Fail 'research-consulted.md must record an ISO access date'
}
$researchUrls = @([regex]::Matches($researchText, 'https://[^\s)>|]+') |
    ForEach-Object { $_.Value.TrimEnd('.', ',', ';') })
if ($researchUrls.Count -eq 0) {
    Fail 'research-consulted.md must record at least one HTTPS source page'
}
foreach ($researchUrl in $researchUrls) {
    try {
        $researchUri = [uri]$researchUrl
    } catch {
        Fail "research-consulted.md contains an invalid URL '$researchUrl'"
    }
    if ($researchUri.Scheme -cne 'https' -or
        ($researchUri.Host -cne 'broadcom.com' -and
         -not $researchUri.Host.EndsWith('.broadcom.com', [System.StringComparison]::OrdinalIgnoreCase))) {
        Fail "research-consulted.md source is not Broadcom-published: '$researchUrl'"
    }
}
foreach ($source in $inventory.sourceProducts) {
    if (-not $researchText.Contains([string]$source.version)) {
        Fail "research-consulted.md must cover '$($source.product)' version '$($source.version)'"
    }
}

function Assert-Equal($Actual, $Expected, [string]$Label) {
    if ([string]$Actual -cne [string]$Expected) {
        Fail "${Label}: expected '$Expected', got '$Actual'"
    }
}

function Assert-IntEqual($Actual, $Expected, [string]$Label) {
    if ([int64]$Actual -ne [int64]$Expected) {
        Fail "${Label}: expected '$Expected', got '$Actual'"
    }
}

function Assert-BoolEqual($Actual, $Expected, [string]$Label) {
    if ([bool]$Actual -ne [bool]$Expected) {
        Fail "${Label}: expected '$Expected', got '$Actual'"
    }
}

function Assert-UniqueSet([object[]]$Actual, [object[]]$Expected, [string]$Label) {
    $actualText = @($Actual | ForEach-Object { [string]$_ })
    $expectedText = @($Expected | ForEach-Object { [string]$_ })
    if (@($actualText | Sort-Object -Unique).Count -ne $actualText.Count) {
        Fail "$Label contains duplicates"
    }
    if (@($expectedText | Sort-Object -Unique).Count -ne $expectedText.Count) {
        Fail "protected authority error: $Label expected set contains duplicates"
    }
    $difference = @(Compare-Object -ReferenceObject @($expectedText | Sort-Object) `
        -DifferenceObject @($actualText | Sort-Object))
    if ($difference.Count -ne 0) {
        $detail = ($difference | ForEach-Object { "$($_.SideIndicator)$($_.InputObject)" }) -join ', '
        Fail "$Label differs: $detail"
    }
}

Assert-Equal $plan.estateId $inventory.estateId 'estateId'
Assert-Equal $plan.planId "$($inventory.estateId)-vcf9-migration" 'planId'
Assert-Equal $plan.authoritySnapshot.id $snapshot.snapshotId 'authoritySnapshot.id'
Assert-Equal $plan.authoritySnapshot.asOf $snapshot.asOf 'authoritySnapshot.asOf'
Assert-Equal $plan.targetBundle.coreVersion $snapshot.targetBundle.coreVersion 'targetBundle.coreVersion'
Assert-Equal $plan.targetBundle.componentLandingVersion $snapshot.targetBundle.componentLandingVersion `
    'targetBundle.componentLandingVersion'
foreach ($field in @('sourceId', 'sourceVersion', 'bundleSupportedSourceCeiling', 'resolution')) {
    Assert-Equal $plan.targetBundle.exception.$field $snapshot.targetBundle.exception.$field `
        "targetBundle.exception.$field"
}
Assert-BoolEqual $plan.targetBundle.exception.directBundleLandingAllowed $false `
    'targetBundle.exception.directBundleLandingAllowed'
Assert-Equal $inventory.foundation.targetBundleVersion $snapshot.targetBundle.coreVersion `
    'fixture target bundle version'

Assert-UniqueSet `
    @($plan.generatedBy.requiredModules | ForEach-Object { "$($_.name)@$($_.minimumVersion)" }) `
    @($snapshot.requiredModules | ForEach-Object { "$($_.name)@$($_.minimumVersion)" }) `
    'generatedBy.requiredModules'

$domain = $inventory.foundation.managementDomain
$topologyChecks = @(
    @('siteId', $inventory.site.id),
    @('deploymentModel', $inventory.foundation.deploymentModel),
    @('domainId', $domain.id),
    @('clusterId', $domain.clusterId),
    @('storageId', $domain.storageId),
    @('networkId', $domain.networkId)
)
foreach ($check in $topologyChecks) {
    Assert-Equal $plan.topology.($check[0]) $check[1] "topology.$($check[0])"
}
$expectedHosts = @($domain.hosts.id)
Assert-IntEqual $plan.topology.hostCount $snapshot.minimumManagementDomainHosts 'topology.hostCount'
Assert-UniqueSet @($plan.topology.hosts) $expectedHosts 'topology.hosts'
if (@($plan.topology.hosts).Count -ne $snapshot.minimumManagementDomainHosts) {
    Fail 'topology must use exactly the pinned minimum management-domain host count'
}

if (@($plan.supportBoundaries).Count -ne @($inventory.sourceProducts).Count -or
    @($snapshot.supportRules).Count -ne @($inventory.sourceProducts).Count) {
    Fail 'supportBoundaries must contain exactly one entry per source product'
}
Assert-UniqueSet @($plan.supportBoundaries.sourceId) @($inventory.sourceProducts.id) `
    'supportBoundary source IDs'
foreach ($source in $inventory.sourceProducts) {
    $actualMatches = @($plan.supportBoundaries | Where-Object sourceId -CEQ $source.id)
    $expectedMatches = @($snapshot.supportRules | Where-Object sourceId -CEQ $source.id)
    if ($actualMatches.Count -ne 1 -or $expectedMatches.Count -ne 1) {
        Fail "support mapping for '$($source.id)' is not one-to-one"
    }
    $actual = $actualMatches[0]
    $expected = $expectedMatches[0]
    foreach ($field in @('sourceProduct', 'sourceVersion', 'targetComponent', 'targetVersion', 'transitionMode', 'endOfGeneralSupport')) {
        Assert-Equal $actual.$field $expected.$field "supportBoundary $($source.id).$field"
    }
    Assert-Equal $actual.sourceProduct $source.product "inventory product $($source.id)"
    Assert-Equal $actual.sourceVersion $source.version "inventory version $($source.id)"
}

if (@($plan.targetComponents).Count -ne 3 -or
    @($plan.targetComponents).Count -ne @($snapshot.targetSizing).Count) {
    Fail 'targetComponents must contain exactly the three required VCF components'
}
Assert-UniqueSet @($plan.targetComponents.component) @($snapshot.targetSizing.component) `
    'target component names'

$steadyVcpu = 0
$steadyMemory = 0
$steadyStorage = 0
foreach ($expected in $snapshot.targetSizing) {
    $matches = @($plan.targetComponents | Where-Object component -CEQ $expected.component)
    if ($matches.Count -ne 1) {
        Fail "target component '$($expected.component)' must appear exactly once"
    }
    $actual = $matches[0]
    foreach ($field in @('version', 'topology', 'size', 'availabilityMode', 'nodeCount', 'sizingBasis')) {
        Assert-Equal $actual.$field $expected.$field "target $($expected.component).$field"
    }
    foreach ($field in @('vCpu', 'memoryGiB', 'storageGiB')) {
        Assert-IntEqual $actual.resourcesPerNode.$field $expected.resourcesPerNode.$field `
            "target $($expected.component).resourcesPerNode.$field"
    }
    foreach ($field in @('objects', 'metrics', 'ingestGiBPerDay', 'activeConnections')) {
        Assert-IntEqual $actual.supportedCapacity.$field $expected.supportedCapacity.$field `
            "target $($expected.component).supportedCapacity.$field"
    }
    Assert-UniqueSet `
        @($actual.placement | ForEach-Object { "$($_.node)@$($_.host)" }) `
        @($expected.placement | ForEach-Object { "$($_.node)@$($_.host)" }) `
        "target $($expected.component) placement"
    if (@($actual.placement).Count -ne [int]$actual.nodeCount) {
        Fail "target $($expected.component) must place every node exactly once"
    }
    if (@($actual.placement | Where-Object host -NotIn $expectedHosts).Count -ne 0) {
        Fail "target $($expected.component) places a node outside the management domain"
    }
    if ($actual.availabilityMode -in @('ha', 'clustered') -and
        @($actual.placement.host | Sort-Object -Unique).Count -ne @($actual.placement).Count) {
        Fail "target $($expected.component) nodes must be separated across hosts"
    }
    $steadyVcpu += [int]$actual.nodeCount * [int]$actual.resourcesPerNode.vCpu
    $steadyMemory += [int]$actual.nodeCount * [int]$actual.resourcesPerNode.memoryGiB
    $steadyStorage += [int]$actual.nodeCount * [int]$actual.resourcesPerNode.storageGiB
}

$operations = @($plan.targetComponents | Where-Object component -CEQ 'VCF Operations')[0]
if ([int]$operations.supportedCapacity.objects -lt [int]$inventory.serviceDemand.operations.objects -or
    [int]$operations.supportedCapacity.metrics -lt [int]$inventory.serviceDemand.operations.collectedMetrics) {
    Fail 'VCF Operations sizing does not cover inventoried demand'
}
$logs = @($plan.targetComponents | Where-Object component -CEQ 'VCF Operations for Logs')[0]
if ([int]$logs.supportedCapacity.ingestGiBPerDay -lt [int]$inventory.serviceDemand.logs.ingestGiBPerDay -or
    [int]$logs.supportedCapacity.activeConnections -lt [int]$inventory.serviceDemand.logs.activeConnections) {
    Fail 'VCF Operations for Logs sizing does not cover inventoried demand'
}

$legacyLogs = @($inventory.sourceProducts | Where-Object id -CEQ 'aria-logs')[0].deployment
$peakVcpu = $steadyVcpu + ([int]$legacyLogs.nodes * [int]$legacyLogs.vCpuPerNode)
$peakMemory = $steadyMemory + ([int]$legacyLogs.nodes * [int]$legacyLogs.memoryGiBPerNode)
$peakStorage = $steadyStorage + ([int]$legacyLogs.nodes * [int]$legacyLogs.storageGiBPerNode)
$available = $domain.availableSuiteHeadroom
$resourceChecks = @(
    @('steadyState', 'vCpu', $steadyVcpu),
    @('steadyState', 'memoryGiB', $steadyMemory),
    @('steadyState', 'storageGiB', $steadyStorage),
    @('parallelCoexistence', 'vCpu', $peakVcpu),
    @('parallelCoexistence', 'memoryGiB', $peakMemory),
    @('parallelCoexistence', 'storageGiB', $peakStorage),
    @('availableHeadroom', 'vCpu', $available.vCpu),
    @('availableHeadroom', 'memoryGiB', $available.memoryGiB),
    @('availableHeadroom', 'storageGiB', $available.storageGiB)
)
foreach ($check in $resourceChecks) {
    Assert-IntEqual $plan.capacity.($check[0]).($check[1]) $check[2] `
        "capacity.$($check[0]).$($check[1])"
}
$fits = $peakVcpu -le [int]$available.vCpu -and
    $peakMemory -le [int]$available.memoryGiB -and
    $peakStorage -le [int]$available.storageGiB
Assert-BoolEqual $plan.capacity.fits $fits 'capacity.fits'
if (-not $fits) { Fail 'the pinned design must fit during parallel Logs coexistence' }

$sourceById = @{}
$inventoryItemType = @{}
$inventoryItemSource = @{}
foreach ($source in $inventory.sourceProducts) {
    $sourceById[$source.id] = $source
    foreach ($item in @($source.content) + @($source.configuration)) {
        if ($inventoryItemType.ContainsKey($item.id)) {
            Fail "protected inventory contains duplicate item '$($item.id)'"
        }
        $inventoryItemType[$item.id] = $item.type
        $inventoryItemSource[$item.id] = $source.id
    }
}

$ruleByItem = @{}
foreach ($rule in $snapshot.contentRules) {
    if ($ruleByItem.ContainsKey($rule.id)) {
        Fail "protected snapshot contains duplicate content rule '$($rule.id)'"
    }
    $ruleByItem[$rule.id] = $rule
}
Assert-UniqueSet @($snapshot.contentRules.id) @($inventoryItemType.Keys) `
    'protected content rule coverage'

if (@($plan.steps).Count -ne @($snapshot.stepRules).Count) {
    Fail 'step count does not match the pinned ordered plan'
}
$seenStepIds = @()
$occurrences = @{}
for ($index = 0; $index -lt @($snapshot.stepRules).Count; $index++) {
    $expected = $snapshot.stepRules[$index]
    $step = $plan.steps[$index]
    foreach ($field in @('order', 'id', 'action')) {
        Assert-Equal $step.$field $expected.$field "step[$index].$field"
    }
    Assert-UniqueSet @($step.dependsOn) @($expected.dependsOn) "step $($step.id) dependencies"
    foreach ($dependency in $step.dependsOn) {
        if ($dependency -notin $seenStepIds) {
            Fail "step '$($step.id)' depends on a missing or later step '$dependency'"
        }
    }
    $seenStepIds += $step.id
    Assert-UniqueSet @($step.sources.sourceId) @($expected.sourceIds) "step $($step.id) sources"
    Assert-UniqueSet @($step.targetComponents) @($expected.targetComponents) `
        "step $($step.id) targets"
    foreach ($sourceRef in $step.sources) {
        if (-not $sourceById.ContainsKey($sourceRef.sourceId)) {
            Fail "step '$($step.id)' names unknown source '$($sourceRef.sourceId)'"
        }
        $source = $sourceById[$sourceRef.sourceId]
        Assert-Equal $sourceRef.product $source.product "step $($step.id) source product"
        Assert-Equal $sourceRef.version $source.version "step $($step.id) source version"
    }
    Assert-UniqueSet @($step.gates.id) @($expected.requiredGateIds) "step $($step.id) gates"
    foreach ($gate in $step.gates) {
        $gateProperty = $snapshot.gateCatalog.PSObject.Properties[$gate.id]
        if ($null -eq $gateProperty) { Fail "step '$($step.id)' uses unknown gate '$($gate.id)'" }
        $catalog = $gateProperty.Value
        foreach ($field in @('phase', 'condition', 'evidence')) {
            Assert-Equal $gate.$field $catalog.$field "gate $($gate.id).$field"
        }
    }
    foreach ($item in @($step.carries) + @($step.abandons)) {
        if ($occurrences.ContainsKey($item.id)) {
            Fail "content/configuration '$($item.id)' is dispositioned more than once"
        }
        $occurrences[$item.id] = $step.id
        if (-not $ruleByItem.ContainsKey($item.id)) {
            Fail "plan dispositions unknown content/configuration '$($item.id)'"
        }
        $rule = $ruleByItem[$item.id]
        if ($rule.sourceId -notin @($step.sources.sourceId)) {
            Fail "item '$($item.id)' is dispositioned by a step that does not name its source"
        }
        Assert-Equal $step.id $rule.stepId "item $($item.id) step"
        Assert-Equal $item.type $inventoryItemType[$item.id] "item $($item.id) type"
        Assert-Equal $item.method $rule.method "item $($item.id) method"
    }
    foreach ($item in $step.carries) {
        Assert-Equal $ruleByItem[$item.id].disposition 'carry' "item $($item.id) disposition"
    }
    foreach ($item in $step.abandons) {
        $rule = $ruleByItem[$item.id]
        Assert-Equal $rule.disposition 'abandon' "item $($item.id) disposition"
        Assert-Equal $item.reasonCode $rule.reasonCode "item $($item.id) reasonCode"
        Assert-Equal $item.reason $rule.reason "item $($item.id) reason"
    }
}
Assert-UniqueSet @($occurrences.Keys) @($inventoryItemType.Keys) 'plan content/configuration coverage'

$moduleRoot = Join-Path $root 'VcfAriaMigration'
$manifestPath = Join-Path $moduleRoot 'VcfAriaMigration.psd1'
$modulePath = Join-Path $moduleRoot 'VcfAriaMigration.psm1'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $modulePath -PathType Leaf)) {
    Fail 'VcfAriaMigration module manifest and root module are required'
}
$manifest = Import-PowerShellDataFile -LiteralPath $manifestPath
Assert-Equal $manifest.RootModule 'VcfAriaMigration.psm1' 'module RootModule'
Assert-UniqueSet @($manifest.FunctionsToExport) @('New-VcfAriaMigrationPlan') 'module exports'
Assert-UniqueSet `
    @($manifest.RequiredModules | ForEach-Object { "$($_.ModuleName)@$($_.ModuleVersion)" }) `
    @($snapshot.requiredModules | ForEach-Object { "$($_.name)@$($_.minimumVersion)" }) `
    'manifest VMware SDK requirements'

$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($modulePath, [ref]$tokens, [ref]$parseErrors)
if (@($parseErrors).Count -ne 0) { Fail "module parse error: $($parseErrors[0].Message)" }
$commandNames = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
}, $true) | ForEach-Object { $_.GetCommandName() } | Where-Object { $null -ne $_ })
foreach ($requiredCommand in @(
    'Import-Module',
    'Initialize-VcfInstallerVcfOperationsNode',
    'Initialize-VcfInstallerVcfOperationsSpec',
    'Initialize-VcfInstallerVcfAutomationSpec'
)) {
    if ($commandNames -cnotcontains $requiredCommand) {
        Fail "module does not use required command '$requiredCommand'"
    }
}
if ($commandNames -ccontains 'Install-Module' -or $commandNames -ccontains 'Save-Module') {
    Fail 'module must not install VMware SDK prerequisites'
}
$vendored = @(Get-ChildItem -LiteralPath $root -Recurse -File | Where-Object {
    $_.Name -like 'VMware.Sdk.Vcf.*' -or $_.Extension -in @('.nupkg', '.dll')
})
if ($vendored.Count -ne 0) { Fail 'VMware SDK modules or binaries must not be vendored' }

$generatedPath = Join-Path $root '.migration-installer-spec.generated.json'
$variantInventoryPath = Join-Path $root '.migration-installer-spec.variant-inventory.json'
$variantSnapshotPath = Join-Path $root '.migration-installer-spec.variant-snapshot.json'
$variantOutputPath = Join-Path $root '.migration-installer-spec.variant-output.json'
try {
    Import-Module -Name $manifestPath -Force -ErrorAction Stop
    New-VcfAriaMigrationPlan `
        -InventoryPath (Join-Path $root 'fixtures/estate-inventory.json') `
        -CompatibilitySnapshotPath (Join-Path $root 'specs/compatibility-snapshot.json') `
        -SchemaPath $schemaPath `
        -OutputPath $generatedPath | Out-Null
    $generatedJson = [System.IO.File]::ReadAllText($generatedPath)
    if (-not (Test-Json -Json $generatedJson -SchemaFile $schemaPath -ErrorAction Stop)) {
        Fail 'module-generated artifact does not satisfy the installer schema'
    }
    if ($generatedJson -cne $artifactJson) {
        Fail 'module output is not byte-identical to migration-installer-spec.json'
    }

    # Prove that both declared data inputs drive generation rather than allowing
    # a module to copy or emit only the committed artifact.
    $variantInventory = $inventory | ConvertTo-Json -Depth 100 | ConvertFrom-Json -Depth 100
    $variantInventory.estateId = 'dal01-management-services-verifier-variant'
    $variantSnapshot = $snapshot | ConvertTo-Json -Depth 100 | ConvertFrom-Json -Depth 100
    $variantSnapshot.snapshotId = 'broadcom-vcf-9.0.1-verifier-variant'
    [System.IO.File]::WriteAllText(
        $variantInventoryPath,
        ($variantInventory | ConvertTo-Json -Depth 100),
        [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText(
        $variantSnapshotPath,
        ($variantSnapshot | ConvertTo-Json -Depth 100),
        [System.Text.UTF8Encoding]::new($false))
    New-VcfAriaMigrationPlan `
        -InventoryPath $variantInventoryPath `
        -CompatibilitySnapshotPath $variantSnapshotPath `
        -SchemaPath $schemaPath `
        -OutputPath $variantOutputPath | Out-Null
    $variantPlan = Get-Content -LiteralPath $variantOutputPath -Raw |
        ConvertFrom-Json -Depth 100
    Assert-Equal $variantPlan.estateId $variantInventory.estateId 'variant estateId'
    Assert-Equal $variantPlan.planId "$($variantInventory.estateId)-vcf9-migration" 'variant planId'
    Assert-Equal $variantPlan.authoritySnapshot.id $variantSnapshot.snapshotId `
        'variant authoritySnapshot.id'
} catch {
    Fail "module execution: $($_.Exception.Message)"
} finally {
    Remove-Item -LiteralPath @(
        $generatedPath,
        $variantInventoryPath,
        $variantSnapshotPath,
        $variantOutputPath
    ) -Force -ErrorAction SilentlyContinue
}

Write-Output 'ALL TESTS PASSED'
