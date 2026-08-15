param(
    [string] $RepositoryRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-True {
    param([bool] $Condition, [string] $Message)
    if (-not $Condition) { throw $Message }
}

function Assert-Equal {
    param($Actual, $Expected, [string] $Message)
    if ($Actual -cne $Expected) {
        throw "$Message (expected '$Expected', got '$Actual')"
    }
}

$planPath = Join-Path $RepositoryRoot 'output/migration-plan.json'
$schemaPath = Join-Path $RepositoryRoot 'specifications/vcf-installer/migration-plan.schema.json'
$inventoryPath = Join-Path $RepositoryRoot 'fixtures/estate-inventory.json'
$snapshotPath = Join-Path $RepositoryRoot 'compatibility/vcf-9.1.0.0-pinned.json'
$modulePath = Join-Path $RepositoryRoot 'src/VcfBrownfieldPlanner/VcfBrownfieldPlanner.psm1'
$manifestPath = Join-Path $RepositoryRoot 'src/VcfBrownfieldPlanner/VcfBrownfieldPlanner.psd1'
$researchPath = Join-Path $RepositoryRoot 'research/sources.md'

# Artifact schema validation is deliberately the first verification operation.
Assert-True (Test-Path -LiteralPath $planPath -PathType Leaf) 'output/migration-plan.json is missing'
Assert-True (Test-Json -LiteralPath $planPath -SchemaFile $schemaPath -ErrorAction Stop) 'migration plan does not validate against the installer migration-plan schema'

Assert-True (Test-Path -LiteralPath $researchPath -PathType Leaf) 'research/sources.md is missing'
$researchText = Get-Content -Raw -LiteralPath $researchPath
$researchEntries = @([regex]::Matches($researchText, '(?ms)^- Title:\s*(?<title>[^\r\n]+)\r?\n\s*Publisher:\s*(?<publisher>[^\r\n]+)\r?\n\s*Access(?:ed| date):\s*(?<date>\d{4}-\d{2}-\d{2})\r?\n\s*URL:\s*(?<url>https://[^\s]+)\r?\n\s*Conclusion used:\s*(?<conclusion>[^\r\n]+)'))
Assert-True ($researchEntries.Count -ge 2) 'research/sources.md must contain at least two complete source records'
$researchUrls = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($entry in $researchEntries) {
    Assert-True ($entry.Groups['title'].Value.Trim().Length -gt 0) 'research source title is empty'
    Assert-True ($entry.Groups['publisher'].Value.Trim() -match '^Broadcom(?:,? Inc\.?)?$') 'research source publisher must identify Broadcom'
    $parsedAccessDate = [datetime]::MinValue
    Assert-True ([datetime]::TryParseExact($entry.Groups['date'].Value, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$parsedAccessDate)) 'research source access date is invalid'
    $sourceUri = [uri]$entry.Groups['url'].Value
    Assert-True ($sourceUri.Scheme -ceq 'https') 'research source URL must use HTTPS'
    Assert-True ($sourceUri.DnsSafeHost -match '(^|\.)broadcom\.com$') 'research source URL must be Broadcom-published'
    Assert-True ($researchUrls.Add($sourceUri.AbsoluteUri)) "duplicate research source URL: $($sourceUri.AbsoluteUri)"
    Assert-True ($entry.Groups['conclusion'].Value.Trim().Length -ge 20) 'research source conclusion is too short'
}

$protectedHashes = @{
    'fixtures/estate-inventory.json' = '864bbcbbbd43519d3575b7c753c7d20416e78d169975b69d7b308b079ab7073d'
    'compatibility/vcf-9.1.0.0-pinned.json' = '66ab3d128bbec84f1ad31ece615bd050ba920dd1d5c62d60c60c0b04d15b2897'
    'specifications/vcf-installer/migration-plan.schema.json' = 'b4041ebec46a64c5363e68b33f0e97802ab4297e2337cb75391f8b2e1704cf7b'
}
foreach ($relativePath in $protectedHashes.Keys) {
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $RepositoryRoot $relativePath)).Hash.ToLowerInvariant()
    Assert-Equal $actualHash $protectedHashes[$relativePath] "protected input changed: $relativePath"
}

$plan = Get-Content -Raw -LiteralPath $planPath | ConvertFrom-Json -Depth 100
$inventory = Get-Content -Raw -LiteralPath $inventoryPath | ConvertFrom-Json -Depth 100
$snapshot = Get-Content -Raw -LiteralPath $snapshotPath | ConvertFrom-Json -Depth 100
$inventoryHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $inventoryPath).Hash.ToLowerInvariant()

Assert-Equal $plan.estateId $inventory.estateId 'estateId does not match inventory'
Assert-Equal $plan.sourceVcfVersion $inventory.vcfVersion 'source VCF version does not match inventory'
Assert-Equal $plan.targetVcfVersion $snapshot.targetVcfVersion 'target VCF version does not match snapshot'
Assert-Equal $plan.inventoryDigest $inventoryHash 'inventory digest is incorrect'
Assert-Equal $plan.compatibilitySnapshot $snapshot.snapshotVersion 'snapshot version is incorrect'
Assert-Equal $plan.strategy $snapshot.requiredStrategy 'strategy is not the pinned supported strategy'
Assert-True (-not [bool]$plan.directInPlaceUpgrade) 'direct in-place upgrade must be false'
Assert-Equal $plan.architecture.sourceDisposition $snapshot.architecture.sourceDisposition 'source disposition is incorrect'
Assert-Equal $plan.architecture.targetTopology $snapshot.architecture.targetTopology 'target topology is incorrect'

$gateById = @{}
foreach ($gate in $plan.gates) {
    Assert-True (-not $gateById.ContainsKey($gate.id)) "duplicate gate id: $($gate.id)"
    $gateById[$gate.id] = $gate
}
foreach ($requiredGate in $snapshot.requiredGates) {
    Assert-True $gateById.ContainsKey($requiredGate.id) "missing required gate: $($requiredGate.id)"
    Assert-Equal $gateById[$requiredGate.id].kind $requiredGate.kind "wrong gate kind for $($requiredGate.id)"
}

Assert-Equal $plan.stages.Count $snapshot.requiredStages.Count 'stage count does not match pinned sequence'
$stageById = @{}
for ($index = 0; $index -lt $snapshot.requiredStages.Count; $index++) {
    $actual = $plan.stages[$index]
    $expected = $snapshot.requiredStages[$index]
    Assert-Equal $actual.sequence $expected.sequence "wrong sequence at stage index $index"
    Assert-Equal $actual.id $expected.id "wrong stage id at index $index"
    Assert-Equal $actual.action $expected.action "wrong stage action for $($expected.id)"
    Assert-Equal (($actual.requires | Sort-Object) -join ',') (($expected.requires | Sort-Object) -join ',') "wrong gates for stage $($expected.id)"
    foreach ($gateId in $actual.requires) {
        Assert-True $gateById.ContainsKey($gateId) "stage $($actual.id) references missing gate $gateId"
    }
    $stageById[$actual.id] = $actual
}

Assert-Equal $plan.components.Count $inventory.components.Count 'plan must name every inventory component exactly once'
$componentById = @{}
foreach ($component in $plan.components) {
    Assert-True (-not $componentById.ContainsKey($component.id)) "duplicate component id: $($component.id)"
    $componentById[$component.id] = $component
}

$blockedTypes = @($snapshot.blockedTransitions.componentType)
foreach ($source in $inventory.components) {
    Assert-True $componentById.ContainsKey($source.id) "inventory component missing from plan: $($source.id)"
    $component = $componentById[$source.id]
    Assert-Equal $component.type $source.type "component type differs for $($source.id)"
    Assert-Equal $component.name $source.name "component name differs for $($source.id)"
    Assert-Equal $component.domain $source.domain "component domain differs for $($source.id)"
    Assert-Equal $component.currentVersion $source.version "installed version differs for $($source.id)"
    Assert-Equal $component.targetVersion $snapshot.targetBom.($source.type) "target version differs for $($source.id)"
    Assert-Equal $component.disposition 'REPLACE_AND_RETIRE_SOURCE' "unsafe disposition for $($source.id)"
    Assert-True $stageById.ContainsKey($component.targetStage) "unknown target stage for $($source.id)"
    $expectedTargetStage = if ($source.domain -ceq 'management-domain') { 'deploy-target-management' } else { 'deploy-target-workload-domain' }
    Assert-Equal $component.targetStage $expectedTargetStage "wrong target stage for $($source.id)"
    Assert-Equal $component.decommissionStage 'decommission-source' "wrong decommission stage for $($source.id)"
    Assert-True ($component.gatedBy.Count -gt 0) "component has no technical gate: $($source.id)"
    foreach ($gateId in $component.gatedBy) {
        Assert-True $gateById.ContainsKey($gateId) "component $($source.id) references missing gate $gateId"
    }
    if ($blockedTypes -contains $source.type) {
        Assert-True ($component.gatedBy -contains 'compat-back-in-time') "blocked component lacks back-in-time gate: $($source.id)"
    }
    Assert-True ($stageById[$component.targetStage].components -contains $source.id) "target stage does not name $($source.id)"
    Assert-True ($stageById['decommission-source'].components -contains $source.id) "decommission stage does not name $($source.id)"
}

Assert-True (Test-Path -LiteralPath $modulePath -PathType Leaf) 'PowerShell module implementation is missing'
$tokens = $null
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($modulePath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) {
    throw "PowerShell module has parse errors: $($parseErrors.Message -join '; ')"
}
$manifest = Import-PowerShellDataFile -LiteralPath $manifestPath
$requiredNames = @($manifest.RequiredModules | ForEach-Object { if ($_ -is [string]) { $_ } else { $_.ModuleName } })
Assert-True ($requiredNames -contains 'VMware.Sdk.Vcf.SddcManager') 'module manifest must depend on VMware.Sdk.Vcf.SddcManager'
$moduleText = Get-Content -Raw -LiteralPath $modulePath
$ast = [System.Management.Automation.Language.Parser]::ParseFile($modulePath, [ref]$tokens, [ref]$parseErrors)
$invokedCommands = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true) | ForEach-Object { $_.GetCommandName() })
$invokedCommandNames = @($invokedCommands | ForEach-Object { ($_ -split '\\')[-1] })
foreach ($commandName in @('Connect-VcfSddcManagerServer', 'Invoke-VcfGetDomains', 'Invoke-VcfGetClusters', 'Invoke-VcfGetHosts')) {
    Assert-True ($invokedCommandNames -ccontains $commandName) "live inventory does not invoke $commandName"
}
foreach ($forbiddenCommand in @('Install-Module', 'Save-Module', 'Install-PSResource', 'Save-PSResource')) {
    Assert-True ($invokedCommandNames -cnotcontains $forbiddenCommand) "solution must not invoke $forbiddenCommand"
}

Import-Module $modulePath -Force
$exportedFunctions = @(Get-Command -Module VcfBrownfieldPlanner -CommandType Function | Select-Object -ExpandProperty Name)
foreach ($functionName in @('Get-VcfSdkEstateInventory', 'New-VcfMigrationPlan', 'Export-VcfMigrationPlan')) {
    Assert-True ($exportedFunctions -ccontains $functionName) "module does not export $functionName"
}
$returnedPlan = New-VcfMigrationPlan -InventoryPath $inventoryPath -CompatibilityPath $snapshotPath
$returnedCanonical = $returnedPlan | ConvertTo-Json -Depth 100 -Compress
$committedCanonical = $plan | ConvertTo-Json -Depth 100 -Compress
Assert-Equal $returnedCanonical $committedCanonical 'New-VcfMigrationPlan does not reproduce output/migration-plan.json'

$generatedPath = Join-Path ([System.IO.Path]::GetTempPath()) "vcf-plan-$([guid]::NewGuid().ToString('N')).json"
try {
    Export-VcfMigrationPlan -InventoryPath $inventoryPath -CompatibilityPath $snapshotPath -Path $generatedPath | Out-Null
    Assert-True (Test-Json -LiteralPath $generatedPath -SchemaFile $schemaPath -ErrorAction Stop) 'module-generated plan fails schema validation'
    $generatedCanonical = (Get-Content -Raw -LiteralPath $generatedPath | ConvertFrom-Json -Depth 100) | ConvertTo-Json -Depth 100 -Compress
    Assert-Equal $generatedCanonical $committedCanonical 'module does not deterministically reproduce output/migration-plan.json'
}
finally {
    Remove-Item -LiteralPath $generatedPath -Force -ErrorAction SilentlyContinue
}

$variantDirectory = Join-Path ([System.IO.Path]::GetTempPath()) "vcf-inputs-$([guid]::NewGuid().ToString('N'))"
try {
    [void](New-Item -ItemType Directory -Path $variantDirectory)
    $variantInventoryPath = Join-Path $variantDirectory 'inventory.json'
    $variantSnapshotPath = Join-Path $variantDirectory 'compatibility.json'
    $variantInventory = Get-Content -Raw -LiteralPath $inventoryPath | ConvertFrom-Json -Depth 100
    $variantInventory.estateId = 'rainpole-vcf-validation'
    $variantInventory.components[0].name = 'sddc-validation.rainpole.example'
    $variantInventory | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $variantInventoryPath -Encoding utf8NoBOM
    $variantSnapshot = Get-Content -Raw -LiteralPath $snapshotPath | ConvertFrom-Json -Depth 100
    $variantSnapshot.snapshotVersion = 'validation-snapshot'
    $variantSnapshot.targetBom.SDDC_MANAGER = '9.1.0.0-validation'
    $variantSnapshot | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $variantSnapshotPath -Encoding utf8NoBOM

    $variantPlan = New-VcfMigrationPlan -InventoryPath $variantInventoryPath -CompatibilityPath $variantSnapshotPath
    $variantHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $variantInventoryPath).Hash.ToLowerInvariant()
    Assert-Equal $variantPlan.estateId 'rainpole-vcf-validation' 'planner ignores the inventory estateId input'
    Assert-Equal $variantPlan.inventoryDigest $variantHash 'planner does not digest the selected inventory input'
    $variantSddcManager = $variantPlan.components | Where-Object id -CEQ 'sddc-m01'
    Assert-Equal $variantSddcManager.name 'sddc-validation.rainpole.example' 'planner ignores inventory component data'
    Assert-Equal $variantPlan.compatibilitySnapshot 'validation-snapshot' 'planner ignores compatibility snapshot metadata'
    Assert-Equal $variantSddcManager.targetVersion '9.1.0.0-validation' 'planner ignores compatibility target BOM data'
}
finally {
    Remove-Item -LiteralPath $variantDirectory -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output 'VCF brownfield migration plan verification passed.'
