Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$artifactPath = Join-Path $root 'migration-installer-spec.json'
$schemaPath = Join-Path $root 'specs/installer-spec.schema.json'

function Fail([string]$Message) {
    [Console]::Error.WriteLine("FAIL: $Message")
    exit 1
}

# The artifact's declared installer schema is the first acceptance contract.
# Do not load the estate, compatibility authority, module, or any other
# submission file until this validation has succeeded.
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
    Fail 'schema validation: migration-installer-spec.json does not satisfy specs/installer-spec.schema.json'
}

# Schema validation has passed. Semantic checks may now consult the artifact,
# local research record, protected inputs, and module source.
$plan = $artifactJson | ConvertFrom-Json -Depth 100
$inventory = Get-Content -LiteralPath (Join-Path $root 'fixtures/estate-inventory.json') -Raw |
    ConvertFrom-Json -Depth 100
$snapshot = Get-Content -LiteralPath (Join-Path $root 'specs/compatibility-snapshot.json') -Raw |
    ConvertFrom-Json -Depth 100

$researchPath = Join-Path $root 'research-sources.md'
if (-not (Test-Path -LiteralPath $researchPath -PathType Leaf)) {
    Fail 'research-sources.md is required'
}
$researchText = [System.IO.File]::ReadAllText($researchPath)
if ($researchText -notmatch '(?i)\baccess(?:ed)?\s*:?[ \t]+[0-9]{4}-[0-9]{2}-[0-9]{2}\b') {
    Fail 'research-sources.md must record an ISO access date'
}
if ($researchText -notmatch 'https://[^\s)>|]+') {
    Fail 'research-sources.md must record at least one HTTPS source page'
}
foreach ($source in $inventory.sourceProducts) {
    if (-not $researchText.Contains([string]$source.version)) {
        Fail "research-sources.md must cover '$($source.product)' version '$($source.version)'"
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

Assert-Equal $plan.authoritySnapshot.id $snapshot.snapshotId 'authority snapshot id'
Assert-Equal $plan.authoritySnapshot.asOf $snapshot.asOf 'authority snapshot date'
Assert-Equal $plan.estateId $inventory.estateId 'estate id'
Assert-Equal $plan.fleetId $inventory.fleet.id 'fleet id'

$scopeFields = @(
    @('managementDomainId', $snapshot.placementAuthority.managementDomainId),
    @('managementDomainChangeMode', $snapshot.placementAuthority.managementDomainChangeMode),
    @('targetDomainId', $snapshot.placementAuthority.targetDomainId),
    @('targetClusterId', $snapshot.placementAuthority.targetClusterId),
    @('targetNetworkId', $snapshot.placementAuthority.targetNetworkId),
    @('targetDatastoreId', $snapshot.placementAuthority.targetDatastoreId)
)
foreach ($pair in $scopeFields) {
    Assert-Equal $plan.scope.($pair[0]) $pair[1] "scope.$($pair[0])"
}
Assert-Equal $inventory.fleet.managementDomain.changeMode 'observe-only' 'fixture management-domain mode'
Assert-Equal $plan.scope.managementDomainChangeMode 'observe-only' 'plan management-domain mode'

Assert-UniqueSet @($plan.sdk.requiredModules | ForEach-Object { "$($_.name)@$($_.minimumVersion)" }) `
    @($snapshot.moduleRequirements | ForEach-Object { "$($_.name)@$($_.minimumVersion)" }) `
    'SDK module requirements'
Assert-UniqueSet @($plan.sdk.commandsUsed) @($snapshot.requiredSdkCommands) 'SDK commands used'

if (@($plan.placements).Count -ne @($snapshot.placements).Count) {
    Fail 'placement count does not match pinned sizing authority'
}
$available = $inventory.fleet.workloadDomain.cluster.availableCapacity
$totalVcpu = 0
$totalMemory = 0
$totalStorage = 0
foreach ($expectedPlacement in $snapshot.placements) {
    $matches = @($plan.placements | Where-Object { $_.component -ceq $expectedPlacement.component })
    if ($matches.Count -ne 1) {
        Fail "placement for '$($expectedPlacement.component)' must appear exactly once"
    }
    $actual = $matches[0]
    foreach ($field in @('version', 'topology', 'nodeCount', 'size', 'availabilityMode')) {
        Assert-Equal $actual.$field $expectedPlacement.$field "placement $($actual.component).$field"
    }
    Assert-Equal $actual.domainId $snapshot.placementAuthority.targetDomainId "placement $($actual.component).domainId"
    Assert-Equal $actual.clusterId $snapshot.placementAuthority.targetClusterId "placement $($actual.component).clusterId"
    Assert-Equal $actual.networkId $snapshot.placementAuthority.targetNetworkId "placement $($actual.component).networkId"
    Assert-Equal $actual.datastoreId $snapshot.placementAuthority.targetDatastoreId "placement $($actual.component).datastoreId"
    if ($actual.domainId -ceq $inventory.fleet.managementDomain.id) {
        Fail "placement $($actual.component) changes the management domain"
    }
    foreach ($field in @('vCpu', 'memoryGiB', 'storageGiB')) {
        Assert-IntEqual $actual.resourcesPerNode.$field $expectedPlacement.resourcesPerNode.$field `
            "placement $($actual.component).resourcesPerNode.$field"
    }
    foreach ($capacityProperty in @($expectedPlacement.capacity.PSObject.Properties |
            ForEach-Object { $_.Name })) {
        Assert-IntEqual $actual.capacity.$capacityProperty $expectedPlacement.capacity.$capacityProperty `
            "placement $($actual.component).capacity.$capacityProperty"
    }
    $totalVcpu += [int]$actual.nodeCount * [int]$actual.resourcesPerNode.vCpu
    $totalMemory += [int]$actual.nodeCount * [int]$actual.resourcesPerNode.memoryGiB
    $totalStorage += [int]$actual.nodeCount * [int]$actual.resourcesPerNode.storageGiB
}

$opsPlacement = @($plan.placements | Where-Object component -ceq 'VCF Operations')[0]
if ([int]$opsPlacement.capacity.objects -lt [int]$inventory.serviceDemand.operations.monitoredObjects -or
    [int]$opsPlacement.capacity.metrics -lt [int]$inventory.serviceDemand.operations.collectedMetrics) {
    Fail 'VCF Operations sizing does not cover the fixture demand'
}
$logsPlacement = @($plan.placements | Where-Object component -ceq 'VCF Operations for Logs')[0]
if ([int]$logsPlacement.capacity.ingestGiBPerDay -lt [int]$inventory.serviceDemand.logs.ingestGiBPerDay -or
    [int]$logsPlacement.capacity.activeSyslogConnections -lt [int]$inventory.serviceDemand.logs.activeSyslogConnections) {
    Fail 'VCF Operations for Logs sizing does not cover the fixture demand'
}

Assert-IntEqual $plan.capacitySummary.required.vCpu $totalVcpu 'capacity summary required vCpu'
Assert-IntEqual $plan.capacitySummary.required.memoryGiB $totalMemory 'capacity summary required memoryGiB'
Assert-IntEqual $plan.capacitySummary.required.storageGiB $totalStorage 'capacity summary required storageGiB'
Assert-IntEqual $plan.capacitySummary.available.vCpu $available.vCpu 'capacity summary available vCpu'
Assert-IntEqual $plan.capacitySummary.available.memoryGiB $available.memoryGiB 'capacity summary available memoryGiB'
Assert-IntEqual $plan.capacitySummary.available.storageGiB $available.storageGiB 'capacity summary available storageGiB'
$fits = $totalVcpu -le [int]$available.vCpu -and
    $totalMemory -le [int]$available.memoryGiB -and
    $totalStorage -le [int]$available.storageGiB
if ([bool]$plan.capacitySummary.withinAvailableCapacity -ne $fits -or -not $fits) {
    Fail 'capacity summary must truthfully fit the target workload-domain cluster'
}

if (@($plan.migrations).Count -ne @($inventory.sourceProducts).Count -or
    @($snapshot.migrations).Count -ne @($inventory.sourceProducts).Count) {
    Fail 'there must be exactly one migration record per fixture source product'
}
Assert-UniqueSet @($plan.migrations.sourceInventoryId) @($inventory.sourceProducts.inventoryId) `
    'migration source inventory ids'

foreach ($source in $inventory.sourceProducts) {
    $authorityMatches = @($snapshot.migrations | Where-Object sourceInventoryId -ceq $source.inventoryId)
    $planMatches = @($plan.migrations | Where-Object sourceInventoryId -ceq $source.inventoryId)
    if ($authorityMatches.Count -ne 1 -or $planMatches.Count -ne 1) {
        Fail "migration mapping for '$($source.inventoryId)' is not one-to-one"
    }
    $authority = $authorityMatches[0]
    $migration = $planMatches[0]
    foreach ($field in @('sourceProduct', 'sourceVersion', 'sourceSupportEnds', 'targetComponent', 'targetVersion', 'path')) {
        Assert-Equal $migration.$field $authority.$field "migration $($source.inventoryId).$field"
    }
    Assert-Equal $migration.sourceProduct $source.product "fixture product $($source.inventoryId)"
    Assert-Equal $migration.sourceVersion $source.version "fixture version $($source.inventoryId)"

    $inventoryItems = @($source.content) + @($source.configuration)
    $inventoryType = @{}
    foreach ($item in $inventoryItems) { $inventoryType[$item.id] = $item.type }
    $authorityIds = @($authority.carryForward.id) + @($authority.abandon.id)
    Assert-UniqueSet $authorityIds @($inventoryItems.id) "protected disposition $($source.inventoryId)"
    $planIds = @($migration.carryForward.id) + @($migration.abandon.id)
    Assert-UniqueSet $planIds @($inventoryItems.id) "plan disposition $($source.inventoryId)"

    Assert-UniqueSet @($migration.carryForward.id) @($authority.carryForward.id) `
        "carry-forward ids $($source.inventoryId)"
    Assert-UniqueSet @($migration.abandon.id) @($authority.abandon.id) `
        "abandoned ids $($source.inventoryId)"
    foreach ($item in $migration.carryForward) {
        Assert-Equal $item.type $inventoryType[$item.id] "carry type $($item.id)"
        $expected = @($authority.carryForward | Where-Object id -ceq $item.id)[0]
        Assert-Equal $item.method $expected.method "carry method $($item.id)"
    }
    foreach ($item in $migration.abandon) {
        Assert-Equal $item.type $inventoryType[$item.id] "abandon type $($item.id)"
        $expected = @($authority.abandon | Where-Object id -ceq $item.id)[0]
        Assert-Equal $item.reason $expected.reason "abandon reason $($item.id)"
    }
}

if (@($plan.steps).Count -ne @($snapshot.steps).Count) {
    Fail 'step count does not match the pinned ordered migration sequence'
}
for ($index = 0; $index -lt @($snapshot.steps).Count; $index++) {
    $expected = $snapshot.steps[$index]
    $step = $plan.steps[$index]
    Assert-IntEqual $step.order ($index + 1) "step[$index].order"
    foreach ($field in @('id', 'action', 'sourceInventoryId', 'targetComponent')) {
        Assert-Equal $step.$field $expected.$field "step[$index].$field"
    }
    Assert-UniqueSet @($step.gates.id) @($expected.requiredGateIds) "step $($step.id) gates"
    foreach ($gate in $step.gates) {
        $catalogEntry = $snapshot.gateCatalog.PSObject.Properties[$gate.id]
        if ($null -eq $catalogEntry) {
            Fail "protected authority error: gate '$($gate.id)' is absent from gateCatalog"
        }
        Assert-Equal $gate.condition $catalogEntry.Value.condition "gate $($gate.id) condition"
        Assert-Equal $gate.evidence $catalogEntry.Value.evidence "gate $($gate.id) evidence"
    }
}
Assert-UniqueSet @($plan.steps.id) @($snapshot.steps.id) 'step ids'

$moduleRoot = Join-Path $root 'VcfAriaMigration'
$manifestPath = Join-Path $moduleRoot 'VcfAriaMigration.psd1'
$modulePath = Join-Path $moduleRoot 'VcfAriaMigration.psm1'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $modulePath -PathType Leaf)) {
    Fail 'VcfAriaMigration module manifest and root module are required'
}
$manifest = Import-PowerShellDataFile -LiteralPath $manifestPath
Assert-Equal $manifest.RootModule 'VcfAriaMigration.psm1' 'module RootModule'
Assert-UniqueSet @($manifest.FunctionsToExport) @('Get-VcfMigrationPlacement', 'New-VcfAriaMigrationPlan') `
    'module exports'
Assert-UniqueSet @($manifest.RequiredModules | ForEach-Object { "$($_.ModuleName)@$($_.ModuleVersion)" }) `
    @($snapshot.moduleRequirements | ForEach-Object { "$($_.name)@$($_.minimumVersion)" }) `
    'manifest SDK requirements'

$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $modulePath, [ref]$tokens, [ref]$parseErrors)
if (@($parseErrors).Count -ne 0) {
    Fail "module parse error: $($parseErrors[0].Message)"
}
$moduleCommands = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
}, $true) | ForEach-Object { $_.GetCommandName() } | Where-Object { $null -ne $_ })
foreach ($command in $snapshot.requiredSdkCommands) {
    if ($moduleCommands -cnotcontains $command) {
        Fail "module does not invoke required SDK command '$command'"
    }
}
foreach ($prohibitedCommand in @('Install-Module', 'Save-Module', 'Install-PSResource', 'Save-PSResource')) {
    if ($moduleCommands -contains $prohibitedCommand) {
        Fail "module must not install or download prerequisites with '$prohibitedCommand'"
    }
}

$placementFunctions = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -ceq 'Get-VcfMigrationPlacement'
}, $true))
if ($placementFunctions.Count -ne 1) {
    Fail 'Get-VcfMigrationPlacement must be defined exactly once'
}
$placementCommands = @($placementFunctions[0].Body.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
}, $true) | ForEach-Object { $_.GetCommandName() } | Where-Object { $null -ne $_ })
foreach ($command in @('Connect-VcfSddcManagerServer', 'Invoke-VcfGetDomains', 'Invoke-VcfGetClusters')) {
    if ($placementCommands -cnotcontains $command) {
        Fail "Get-VcfMigrationPlacement does not invoke required discovery command '$command'"
    }
}

$moduleFunctions = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst]
}, $true))
$planFunctions = @($moduleFunctions | Where-Object Name -ceq 'New-VcfAriaMigrationPlan')
if ($planFunctions.Count -ne 1) {
    Fail 'New-VcfAriaMigrationPlan must be defined exactly once'
}
$reachableCommands = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase)
$visitedFunctions = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase)
$functionQueue = [System.Collections.Generic.Queue[object]]::new()
$functionQueue.Enqueue($planFunctions[0])
while ($functionQueue.Count -gt 0) {
    $functionAst = $functionQueue.Dequeue()
    if (-not $visitedFunctions.Add($functionAst.Name)) { continue }
    $bodyCommands = @($functionAst.Body.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst]
    }, $true) | ForEach-Object { $_.GetCommandName() } | Where-Object { $null -ne $_ })
    foreach ($bodyCommand in $bodyCommands) {
        $null = $reachableCommands.Add($bodyCommand)
        foreach ($calledFunction in @($moduleFunctions | Where-Object Name -ieq $bodyCommand)) {
            $functionQueue.Enqueue($calledFunction)
        }
    }
}
foreach ($initializer in @($snapshot.requiredSdkCommands | Where-Object { $_ -like 'Initialize-*' })) {
    if (-not $reachableCommands.Contains($initializer)) {
        Fail "New-VcfAriaMigrationPlan does not reach required initializer '$initializer'"
    }
}

$vendored = @(Get-ChildItem -LiteralPath $root -Recurse -File | Where-Object {
    $_.Name -like 'VMware.Sdk.Vcf.*' -or $_.Extension -in @('.nupkg', '.dll')
})
if ($vendored.Count -ne 0) {
    Fail 'VMware SDK modules or binaries must not be vendored in the submission'
}

$generatedPath = Join-Path $root '.vcf-migration-generated.json'
$variantInventoryPath = Join-Path $root '.vcf-migration-variant-inventory.json'
$variantSnapshotPath = Join-Path $root '.vcf-migration-variant-snapshot.json'
$variantOutputPath = Join-Path $root '.vcf-migration-variant-output.json'
try {
    Import-Module -Name $manifestPath -Force -ErrorAction Stop
    New-VcfAriaMigrationPlan `
        -InventoryPath (Join-Path $root 'fixtures/estate-inventory.json') `
        -CompatibilitySnapshotPath (Join-Path $root 'specs/compatibility-snapshot.json') `
        -OutputPath $generatedPath
    $generatedJson = [System.IO.File]::ReadAllText($generatedPath)
    if (-not (Test-Json -Json $generatedJson -SchemaFile $schemaPath -ErrorAction Stop)) {
        Fail 'module-generated artifact does not satisfy the installer schema'
    }
    if ($generatedJson -cne $artifactJson) {
        Fail 'module output is not byte-identical to the committed migration-installer-spec.json'
    }

    # Prove that both declared input paths drive generation rather than allowing
    # a module to copy or emit only the committed protected answer.
    $variantInventory = $inventory | ConvertTo-Json -Depth 100 | ConvertFrom-Json -Depth 100
    $variantInventory.estateId = 'northstar-central-verifier-variant'
    $variantInventory.fleet.id = 'fleet-central-verifier-variant'
    $variantSnapshot = $snapshot | ConvertTo-Json -Depth 100 | ConvertFrom-Json -Depth 100
    $variantSnapshot.snapshotId = 'broadcom-vcf-9.0.2-verifier-variant'
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
        -OutputPath $variantOutputPath
    $variantPlan = Get-Content -LiteralPath $variantOutputPath -Raw | ConvertFrom-Json -Depth 100
    Assert-Equal $variantPlan.estateId $variantInventory.estateId 'variant estate id'
    Assert-Equal $variantPlan.fleetId $variantInventory.fleet.id 'variant fleet id'
    Assert-Equal $variantPlan.authoritySnapshot.id $variantSnapshot.snapshotId 'variant authority id'
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
