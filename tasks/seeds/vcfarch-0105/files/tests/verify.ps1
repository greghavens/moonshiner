Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$workspace = Split-Path -Parent $PSScriptRoot
$sddcPath = Join-Path $workspace 'output/sddc-spec.json'
$planPath = Join-Path $workspace 'output/migration-plan.json'
$openApiPath = Join-Path $workspace 'specifications/vcf-installer/vcf-installer-openapi.json'
$planSchemaPath = Join-Path $workspace 'schemas/migration-plan.schema.json'
$inventoryPath = Join-Path $workspace 'fixtures/estate-inventory.json'
$snapshotPath = Join-Path $workspace 'fixtures/compatibility-snapshot.json'
$researchPath = Join-Path $workspace 'research-sources.json'

function Assert-Condition {
    param(
        [Parameter(Mandatory)] [bool] $Condition,
        [Parameter(Mandatory)] [string] $Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Assert-Value {
    param(
        $Actual,
        $Expected,
        [Parameter(Mandatory)] [string] $Label
    )

    if ($Actual -cne $Expected) {
        throw "${Label}: expected '$Expected', got '$Actual'"
    }
}

function Assert-Set {
    param(
        [AllowEmptyCollection()] [object[]] $Actual,
        [AllowEmptyCollection()] [object[]] $Expected,
        [Parameter(Mandatory)] [string] $Label
    )

    $actualStrings = @($Actual | ForEach-Object { [string] $_ } | Sort-Object)
    $expectedStrings = @($Expected | ForEach-Object { [string] $_ } | Sort-Object)
    $difference = @(Compare-Object -ReferenceObject $expectedStrings -DifferenceObject $actualStrings -CaseSensitive)
    if ($difference.Count -ne 0) {
        throw "$Label does not match. Expected [$($expectedStrings -join ', ')], got [$($actualStrings -join ', ')]"
    }
}

function Read-Json {
    param([Parameter(Mandatory)] [string] $Path)

    return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -Depth 100
}

function Assert-SddcSpecSchema {
    param([Parameter(Mandatory)] [string] $CandidatePath)

    if (-not (Test-Path -LiteralPath $CandidatePath -PathType Leaf)) {
        throw "Missing SddcSpec artifact: $CandidatePath"
    }

    $openApi = Read-Json -Path $openApiPath
    $rootSchema = [ordered]@{
        '$schema' = 'http://json-schema.org/draft-07/schema#'
        '$ref' = '#/components/schemas/SddcSpec'
        components = $openApi.components
    } | ConvertTo-Json -Depth 100 -Compress
    $candidate = Get-Content -LiteralPath $CandidatePath -Raw

    $valid = Test-Json -Json $candidate -Schema $rootSchema -ErrorAction Stop
    if (-not $valid) {
        throw 'sddc-spec.json does not validate against components.schemas.SddcSpec in the pinned VCF Installer OpenAPI document.'
    }
}

function Assert-PlanSchema {
    param([Parameter(Mandatory)] [string] $CandidatePath)

    if (-not (Test-Path -LiteralPath $CandidatePath -PathType Leaf)) {
        throw "Missing migration plan artifact: $CandidatePath"
    }

    $candidate = Get-Content -LiteralPath $CandidatePath -Raw
    $schema = Get-Content -LiteralPath $planSchemaPath -Raw
    $valid = Test-Json -Json $candidate -Schema $schema -ErrorAction Stop
    if (-not $valid) {
        throw 'migration-plan.json does not validate against schemas/migration-plan.schema.json.'
    }
}

function ConvertTo-CanonicalObject {
    param($InputObject)

    if ($null -eq $InputObject) {
        return $null
    }

    if ($InputObject -is [System.Collections.IDictionary]) {
        $ordered = [ordered]@{}
        foreach ($key in @($InputObject.Keys | Sort-Object)) {
            $ordered[[string] $key] = ConvertTo-CanonicalObject -InputObject $InputObject[$key]
        }
        return [pscustomobject] $ordered
    }

    if ($InputObject -is [pscustomobject]) {
        $ordered = [ordered]@{}
        foreach ($property in @($InputObject.PSObject.Properties | Sort-Object Name)) {
            $ordered[$property.Name] = ConvertTo-CanonicalObject -InputObject $property.Value
        }
        return [pscustomobject] $ordered
    }

    if (($InputObject -is [System.Collections.IEnumerable]) -and -not ($InputObject -is [string])) {
        return @($InputObject | ForEach-Object { ConvertTo-CanonicalObject -InputObject $_ })
    }

    return $InputObject
}

function Get-CanonicalJson {
    param([Parameter(Mandatory)] [string] $Path)

    $object = Read-Json -Path $Path
    return ConvertTo-CanonicalObject -InputObject $object | ConvertTo-Json -Depth 100 -Compress
}

function Assert-ResearchSources {
    param(
        [Parameter(Mandatory)] [string] $CandidatePath,
        [Parameter(Mandatory)] $Inventory
    )

    Assert-Condition -Condition (Test-Path -LiteralPath $CandidatePath -PathType Leaf) -Message 'Missing research-sources.json.'
    $sources = @(Read-Json -Path $CandidatePath)
    Assert-Condition -Condition ($sources.Count -gt 0) -Message 'research-sources.json must contain at least one consulted source.'

    foreach ($source in $sources) {
        foreach ($propertyName in @('title', 'url', 'consultedAt', 'usedFor')) {
            $property = $source.PSObject.Properties[$propertyName]
            Assert-Condition -Condition ($null -ne $property -and -not [string]::IsNullOrWhiteSpace([string] $property.Value)) -Message "Research source is missing $propertyName."
        }

        $uri = $null
        $isAbsoluteWebUrl = [System.Uri]::TryCreate([string] $source.url, [System.UriKind]::Absolute, [ref] $uri) -and @('http', 'https') -ccontains $uri.Scheme
        Assert-Condition -Condition $isAbsoluteWebUrl -Message "Research source URL must be an absolute HTTP(S) URL: $($source.url)"
        Assert-Condition -Condition (-not $uri.Host.EndsWith('.invalid', [System.StringComparison]::OrdinalIgnoreCase)) -Message "Research source URL cannot use the reserved .invalid domain: $($source.url)"
        $consultedAt = [datetimeoffset]::MinValue
        $hasTimestamp = $source.consultedAt -is [datetime] -or [datetimeoffset]::TryParse(
            [string] $source.consultedAt,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::AssumeUniversal,
            [ref] $consultedAt
        )
        Assert-Condition -Condition $hasTimestamp -Message "Research consultedAt must be a parseable timestamp: $($source.consultedAt)"
    }

    $researchPurpose = (@($sources.usedFor) -join "`n")
    foreach ($product in @($Inventory.components.product | Sort-Object -Unique)) {
        Assert-Condition -Condition ($researchPurpose.Contains([string] $product, [System.StringComparison]::OrdinalIgnoreCase)) -Message "Research sources do not identify their use for $product."
    }
}

# The installer schema validation is deliberately the first acceptance check.
Assert-SddcSpecSchema -CandidatePath $sddcPath
Write-Host 'PASS: submitted SddcSpec validates against the pinned VCF Installer schema'

Assert-PlanSchema -CandidatePath $planPath

$openApi = Read-Json -Path $openApiPath
$inventory = Read-Json -Path $inventoryPath
$snapshot = Read-Json -Path $snapshotPath
$sddc = Read-Json -Path $sddcPath
$plan = Read-Json -Path $planPath
$design = $inventory.greenfieldDesign
$requirements = $inventory.requirements

Assert-ResearchSources -CandidatePath $researchPath -Inventory $inventory

Assert-Value -Actual $openApi.info.version -Expected '9.1.0.0' -Label 'Pinned installer specification version'
Assert-Value -Actual $sddc.sddcId -Expected $design.sddcId -Label 'SddcSpec sddcId'
Assert-Value -Actual $sddc.workflowType -Expected $design.workflowType -Label 'SddcSpec workflowType'
Assert-Value -Actual $sddc.version -Expected $inventory.targetFleetVersion -Label 'SddcSpec version'
Assert-Value -Actual $sddc.vcfInstanceName -Expected $design.vcfInstanceName -Label 'SddcSpec VCF instance name'

Assert-Set -Actual @($sddc.hostSpecs.hostname) -Expected @($design.hostnames) -Label 'SddcSpec hostnames'
Assert-Value -Actual @($sddc.hostSpecs).Count -Expected @($design.hostnames).Count -Label 'SddcSpec host count'
foreach ($hostSpec in @($sddc.hostSpecs)) {
    Assert-Value -Actual $hostSpec.credentials.username -Expected $design.hostCredential.username -Label "Credentials user for $($hostSpec.hostname)"
    Assert-Value -Actual $hostSpec.credentials.password -Expected $design.hostCredential.password -Label "Credentials password for $($hostSpec.hostname)"
}

Assert-Value -Actual $sddc.vcenterSpec.vcenterHostname -Expected $design.vcenter.hostname -Label 'vCenter hostname'
Assert-Value -Actual $sddc.vcenterSpec.rootVcenterPassword -Expected $design.vcenter.rootPassword -Label 'vCenter root password'
Assert-Value -Actual $sddc.vcenterSpec.version -Expected $inventory.targetFleetVersion -Label 'vCenter version'
Assert-Value -Actual $sddc.vcenterSpec.useExistingDeployment -Expected $false -Label 'vCenter greenfield flag'
Assert-Value -Actual $sddc.vcenterSpec.vmSize -Expected $design.vcenter.vmSize -Label 'vCenter size'
Assert-Value -Actual $sddc.vcenterSpec.storageSize -Expected $design.vcenter.storageSize -Label 'vCenter storage size'
Assert-Value -Actual $sddc.vcenterSpec.ssoDomain -Expected $design.vcenter.ssoDomain -Label 'vCenter SSO domain'

Assert-Value -Actual $sddc.clusterSpec.datacenterName -Expected $design.cluster.datacenterName -Label 'Datacenter name'
Assert-Value -Actual $sddc.clusterSpec.clusterName -Expected $design.cluster.clusterName -Label 'Cluster name'
Assert-Value -Actual $sddc.sddcManagerSpec.hostname -Expected $design.sddcManager.hostname -Label 'SDDC Manager hostname'
Assert-Value -Actual $sddc.sddcManagerSpec.rootPassword -Expected $design.sddcManager.rootPassword -Label 'SDDC Manager root password'
Assert-Value -Actual $sddc.sddcManagerSpec.sshPassword -Expected $design.sddcManager.sshPassword -Label 'SDDC Manager SSH password'
Assert-Value -Actual $sddc.sddcManagerSpec.localUserPassword -Expected $design.sddcManager.localUserPassword -Label 'SDDC Manager local-user password'
Assert-Value -Actual $sddc.sddcManagerSpec.version -Expected $inventory.targetFleetVersion -Label 'SDDC Manager version'
Assert-Value -Actual $sddc.sddcManagerSpec.useExistingDeployment -Expected $false -Label 'SDDC Manager greenfield flag'
Assert-Value -Actual $sddc.licenseServerSpec.hostname -Expected $design.licenseServer.hostname -Label 'License Server hostname'
Assert-Value -Actual $sddc.licenseServerSpec.version -Expected $inventory.targetFleetVersion -Label 'License Server version'
Assert-Value -Actual $sddc.licenseServerSpec.useExistingDeployment -Expected $false -Label 'License Server greenfield flag'

Assert-Set -Actual @($sddc.nsxtSpec.nsxtManagers.hostname) -Expected @($design.nsx.managerHostnames) -Label 'NSX manager hostnames'
Assert-Value -Actual $sddc.nsxtSpec.vipFqdn -Expected $design.nsx.vipFqdn -Label 'NSX VIP'
Assert-Value -Actual $sddc.nsxtSpec.nsxtManagerSize -Expected $design.nsx.managerSize -Label 'NSX manager size'
Assert-Value -Actual $sddc.nsxtSpec.transportVlanId -Expected $design.nsx.transportVlanId -Label 'NSX transport VLAN'
Assert-Value -Actual $sddc.nsxtSpec.version -Expected $inventory.targetFleetVersion -Label 'NSX version'
Assert-Value -Actual $sddc.nsxtSpec.useExistingDeployment -Expected $false -Label 'NSX greenfield flag'
Assert-Value -Actual $sddc.nsxtSpec.ipAddressPoolSpec.name -Expected $design.nsx.tepPool.name -Label 'NSX TEP pool name'
$tepSubnet = @($sddc.nsxtSpec.ipAddressPoolSpec.subnets)
Assert-Value -Actual $tepSubnet.Count -Expected 1 -Label 'NSX TEP subnet count'
Assert-Value -Actual $tepSubnet[0].cidr -Expected $design.nsx.tepPool.cidr -Label 'NSX TEP CIDR'
Assert-Value -Actual $tepSubnet[0].gateway -Expected $design.nsx.tepPool.gateway -Label 'NSX TEP gateway'
Assert-Value -Actual @($tepSubnet[0].ipAddressPoolRanges).Count -Expected 1 -Label 'NSX TEP range count'
Assert-Value -Actual $tepSubnet[0].ipAddressPoolRanges[0].start -Expected $design.nsx.tepPool.start -Label 'NSX TEP range start'
Assert-Value -Actual $tepSubnet[0].ipAddressPoolRanges[0].end -Expected $design.nsx.tepPool.end -Label 'NSX TEP range end'

Assert-Value -Actual $sddc.dnsSpec.subdomain -Expected $design.domainName -Label 'DNS subdomain'
Assert-Set -Actual @($sddc.dnsSpec.nameservers) -Expected @($design.dnsServers) -Label 'DNS servers'
Assert-Set -Actual @($sddc.ntpServers) -Expected @($design.ntpServers) -Label 'NTP servers'

Assert-Value -Actual @($sddc.networkSpecs).Count -Expected @($design.networks).Count -Label 'Network count'
foreach ($expectedNetwork in @($design.networks)) {
    $actualNetworks = @($sddc.networkSpecs | Where-Object networkType -CEQ $expectedNetwork.networkType)
    Assert-Value -Actual $actualNetworks.Count -Expected 1 -Label "Network occurrence for $($expectedNetwork.networkType)"
    $actualNetwork = $actualNetworks[0]
    Assert-Value -Actual $actualNetwork.vlanId -Expected $expectedNetwork.vlanId -Label "$($expectedNetwork.networkType) VLAN"
    Assert-Value -Actual $actualNetwork.subnet -Expected $expectedNetwork.subnet -Label "$($expectedNetwork.networkType) subnet"
    Assert-Value -Actual $actualNetwork.gateway -Expected $expectedNetwork.gateway -Label "$($expectedNetwork.networkType) gateway"
    Assert-Value -Actual $actualNetwork.subnetMask -Expected $expectedNetwork.subnetMask -Label "$($expectedNetwork.networkType) mask"
    Assert-Value -Actual $actualNetwork.mtu -Expected $expectedNetwork.mtu -Label "$($expectedNetwork.networkType) MTU"
    Assert-Value -Actual $actualNetwork.includeIpAddressRanges[0].startIpAddress -Expected $expectedNetwork.start -Label "$($expectedNetwork.networkType) range start"
    Assert-Value -Actual $actualNetwork.includeIpAddressRanges[0].endIpAddress -Expected $expectedNetwork.end -Label "$($expectedNetwork.networkType) range end"
    Assert-Value -Actual @($actualNetwork.includeIpAddressRanges).Count -Expected 1 -Label "$($expectedNetwork.networkType) range count"
}

Assert-Value -Actual @($sddc.dvsSpecs).Count -Expected 1 -Label 'Distributed switch count'
$dvs = @($sddc.dvsSpecs)[0]
Assert-Value -Actual $dvs.dvsName -Expected $design.distributedSwitch.name -Label 'Distributed switch name'
Assert-Value -Actual $dvs.mtu -Expected $design.distributedSwitch.mtu -Label 'Distributed switch MTU'
$actualNicMappings = @($dvs.vmnicsToUplinks | ForEach-Object { "$($_.id)=$($_.uplink)" })
$expectedNicMappings = @($design.distributedSwitch.vmnicUplinks | ForEach-Object { "$($_.id)=$($_.uplink)" })
Assert-Set -Actual $actualNicMappings -Expected $expectedNicMappings -Label 'Distributed switch vmnic mappings'
Assert-Set -Actual @($dvs.networks) -Expected @($design.networks.networkType) -Label 'Distributed switch networks'

Assert-Value -Actual $sddc.datastoreSpec.vsanSpec.datastoreName -Expected $design.vsan.datastoreName -Label 'vSAN datastore name'
Assert-Value -Actual $sddc.datastoreSpec.vsanSpec.esaConfig.enabled -Expected $design.vsan.esaEnabled -Label 'vSAN ESA setting'
Assert-Value -Actual $sddc.datastoreSpec.vsanSpec.failuresToTolerate -Expected $design.vsan.failuresToTolerate -Label 'vSAN failures to tolerate'
Assert-Value -Actual $sddc.vspClusterSpec.platformFqdn -Expected $design.managementServices.platformFqdn -Label 'VSP platform FQDN'
Assert-Value -Actual $sddc.vspClusterSpec.instanceFqdn -Expected $design.managementServices.instanceFqdn -Label 'VSP instance FQDN'
Assert-Value -Actual $sddc.vspClusterSpec.fleetFqdn -Expected $design.managementServices.fleetFqdn -Label 'VSP fleet FQDN'
Assert-Value -Actual $sddc.vspClusterSpec.systemUserPassword -Expected $design.managementServices.systemUserPassword -Label 'VSP system-user password'
Assert-Value -Actual $sddc.vspClusterSpec.ipv4Pool.cidr -Expected $design.managementServices.ipv4Cidr -Label 'VSP IPv4 CIDR'
Assert-Value -Actual $sddc.vspClusterSpec.ipv4Pool.ipRange.startIpAddress -Expected $design.managementServices.ipv4Start -Label 'VSP IPv4 range start'
Assert-Value -Actual $sddc.vspClusterSpec.ipv4Pool.ipRange.endIpAddress -Expected $design.managementServices.ipv4End -Label 'VSP IPv4 range end'
Assert-Value -Actual $sddc.vspClusterSpec.size -Expected $design.managementServices.size -Label 'VSP size'
Assert-Value -Actual $sddc.vspClusterSpec.version -Expected $inventory.targetFleetVersion -Label 'VSP version'
Assert-Value -Actual $sddc.vspClusterSpec.useExistingDeployment -Expected $false -Label 'VSP greenfield flag'
Assert-Value -Actual $sddc.fleetLcmSpec.version -Expected $inventory.targetFleetVersion -Label 'Fleet LCM version'
Assert-Value -Actual $sddc.sddcLcmSpec.version -Expected $inventory.targetFleetVersion -Label 'SDDC LCM version'
Assert-Value -Actual $sddc.fleetDepotSpec.version -Expected $inventory.targetFleetVersion -Label 'Fleet depot version'

Assert-Value -Actual $plan.schemaVersion -Expected '1.0' -Label 'Migration schema version'
Assert-Value -Actual $plan.estateId -Expected $inventory.estateId -Label 'Migration estate ID'
Assert-Value -Actual $plan.targetFleetVersion -Expected $inventory.targetFleetVersion -Label 'Migration target fleet'

$targetCombinations = @($snapshot.targetCombinations | Where-Object fleetVersion -CEQ $inventory.targetFleetVersion)
Assert-Value -Actual $targetCombinations.Count -Expected 1 -Label 'Pinned target combination count'
$targetCombination = $targetCombinations[0]

Assert-Value -Actual @($plan.steps).Count -Expected @($inventory.components).Count -Label 'Migration step count'
Assert-Set -Actual @($plan.steps.componentId) -Expected @($inventory.components.componentId) -Label 'Migration components'

$orderedSteps = @($plan.steps | Sort-Object order)
for ($index = 0; $index -lt $orderedSteps.Count; $index++) {
    Assert-Value -Actual $orderedSteps[$index].order -Expected ($index + 1) -Label 'Migration order sequence'
}

foreach ($component in @($inventory.components)) {
    $componentSteps = @($plan.steps | Where-Object componentId -CEQ $component.componentId)
    Assert-Value -Actual $componentSteps.Count -Expected 1 -Label "Step count for $($component.componentId)"
    $step = $componentSteps[0]
    Assert-Value -Actual $step.product -Expected $component.product -Label "Product for $($component.componentId)"
    Assert-Value -Actual $step.currentVersion -Expected $component.currentVersion -Label "Current version for $($component.componentId)"

    $targetProperty = $targetCombination.products.PSObject.Properties[$component.product]
    Assert-Condition -Condition ($null -ne $targetProperty) -Message "No pinned target for product $($component.product)"
    $expectedTarget = [string] $targetProperty.Value
    Assert-Value -Actual $step.targetVersion -Expected $expectedTarget -Label "Target version for $($component.componentId)"

    $rules = @($snapshot.upgradePaths | Where-Object {
        $_.product -ceq $component.product -and
        $_.fromVersion -ceq $component.currentVersion -and
        $_.toVersion -ceq $expectedTarget
    })
    Assert-Value -Actual $rules.Count -Expected 1 -Label "Pinned upgrade rule count for $($component.componentId)"
    $rule = $rules[0]
    Assert-Set -Actual @($step.gates) -Expected @($rule.requiredGates) -Label "Technical gates for $($component.componentId)"

    $actualPath = @($step.upgradePath)
    $expectedPath = @($rule.path)
    Assert-Value -Actual $actualPath.Count -Expected $expectedPath.Count -Label "Upgrade path length for $($component.componentId)"
    for ($pathIndex = 0; $pathIndex -lt $expectedPath.Count; $pathIndex++) {
        Assert-Value -Actual $actualPath[$pathIndex] -Expected $expectedPath[$pathIndex] -Label "Upgrade path item $pathIndex for $($component.componentId)"
    }

}

foreach ($constraint in @($snapshot.orderingConstraints)) {
    $before = @($plan.steps | Where-Object product -CEQ $constraint.beforeProduct)
    $after = @($plan.steps | Where-Object product -CEQ $constraint.afterProduct)
    Assert-Condition -Condition ($before.Count -gt 0 -and $after.Count -gt 0) -Message "Ordering constraint references a missing product: $($constraint.beforeProduct) -> $($constraint.afterProduct)"
    $maxBefore = ($before.order | Measure-Object -Maximum).Maximum
    $minAfter = ($after.order | Measure-Object -Minimum).Minimum
    Assert-Condition -Condition ($maxBefore -lt $minAfter) -Message "$($constraint.beforeProduct) must precede $($constraint.afterProduct)"
}

$edge = $plan.edgeDesign
Assert-Value -Actual $edge.requiredNorthSouthGbps -Expected $requirements.northSouthThroughputGbps -Label 'Edge throughput requirement'
Assert-Value -Actual $edge.surviveSingleNodeFailure -Expected $requirements.surviveSingleEdgeFailure -Label 'Edge failure requirement'
$eligibleProfiles = @($snapshot.edgeSizing.profiles | Where-Object singleNodeCapacityGbps -GE $requirements.northSouthThroughputGbps | Sort-Object singleNodeCapacityGbps)
Assert-Condition -Condition ($eligibleProfiles.Count -gt 0) -Message 'Pinned Edge sizing table has no eligible profile.'
Assert-Value -Actual $edge.formFactor -Expected $eligibleProfiles[0].formFactor -Label 'Edge form factor'
Assert-Value -Actual $edge.nodeCount -Expected @($requirements.edgeNodes).Count -Label 'Edge node count'
Assert-Condition -Condition ($edge.nodeCount -ge $snapshot.edgeSizing.minimumNodesForSingleFailure) -Message 'Edge node count does not satisfy single-node failure sizing.'
Assert-Value -Actual @($edge.uplinks).Count -Expected ($edge.nodeCount * $requirements.uplinksPerEdge) -Label 'Edge uplink count'

foreach ($edgeNode in @($requirements.edgeNodes)) {
    $nodeUplinks = @($edge.uplinks | Where-Object edgeNode -CEQ $edgeNode)
    Assert-Value -Actual $nodeUplinks.Count -Expected $requirements.uplinksPerEdge -Label "Uplink count for $edgeNode"
    Assert-Set -Actual @($nodeUplinks.name) -Expected @('uplink1', 'uplink2') -Label "Uplink names for $edgeNode"
    Assert-Set -Actual @($nodeUplinks.leafSwitch) -Expected @($requirements.leafSwitches) -Label "Leaf diversity for $edgeNode"
    foreach ($uplink in $nodeUplinks) {
        Assert-Value -Actual $uplink.speedGbps -Expected $requirements.uplinkSpeedGbps -Label "Speed for $edgeNode/$($uplink.name)"
        Assert-Value -Actual $uplink.teamingPolicy -Expected $requirements.teamingPolicy -Label "Teaming for $edgeNode/$($uplink.name)"
        Assert-Set -Actual @($uplink.trunkVlans) -Expected @($requirements.trunkVlans) -Label "VLAN trunks for $edgeNode/$($uplink.name)"
    }
    $aggregateSpeed = ($nodeUplinks.speedGbps | Measure-Object -Sum).Sum
    Assert-Condition -Condition ($aggregateSpeed -ge $requirements.northSouthThroughputGbps) -Message "$edgeNode uplinks cannot carry the stated throughput."
}

$manifestPath = Join-Path $workspace 'VcfFleetArchitecture/VcfFleetArchitecture.psd1'
$modulePath = Join-Path $workspace 'VcfFleetArchitecture/VcfFleetArchitecture.psm1'
Assert-Condition -Condition (Test-Path -LiteralPath $manifestPath -PathType Leaf) -Message 'Missing VcfFleetArchitecture module manifest.'
Assert-Condition -Condition (Test-Path -LiteralPath $modulePath -PathType Leaf) -Message 'Missing VcfFleetArchitecture module implementation.'
$manifest = Import-PowerShellDataFile -LiteralPath $manifestPath
Assert-Value -Actual $manifest.RootModule -Expected 'VcfFleetArchitecture.psm1' -Label 'Module RootModule'
Assert-Condition -Condition (@($manifest.FunctionsToExport) -ccontains 'New-VcfFleetArchitecture') -Message 'Module manifest must export New-VcfFleetArchitecture.'
$requiredModuleNames = @($manifest.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ }
    elseif ($null -ne $_.ModuleName) { $_.ModuleName }
})
Assert-Condition -Condition ($requiredModuleNames -ccontains 'VMware.Sdk.Vcf.SddcManager') -Message 'Module manifest must require VMware.Sdk.Vcf.SddcManager.'

$moduleTokens = $null
$moduleParseErrors = $null
$moduleAst = [System.Management.Automation.Language.Parser]::ParseFile($modulePath, [ref] $moduleTokens, [ref] $moduleParseErrors)
Assert-Value -Actual @($moduleParseErrors).Count -Expected 0 -Label 'Module parse-error count'
$moduleImports = @($moduleAst.FindAll({
    param($ast)
    $ast -is [System.Management.Automation.Language.CommandAst] -and $ast.GetCommandName() -ceq 'Import-Module'
}, $true))
Assert-Condition -Condition ($moduleImports.Count -gt 0) -Message 'Module implementation must import its declared VMware SDK prerequisite when available.'

Import-Module -Name $modulePath -Force
$command = Get-Command -Name New-VcfFleetArchitecture -CommandType Function -ErrorAction Stop
foreach ($parameterName in @('InventoryPath', 'CompatibilitySnapshotPath', 'OutputDirectory')) {
    Assert-Condition -Condition $command.Parameters.ContainsKey($parameterName) -Message "New-VcfFleetArchitecture is missing -$parameterName."
}

$tempOutput = Join-Path ([System.IO.Path]::GetTempPath()) ("vcfarch-verify-" + [System.IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $tempOutput | Out-Null
try {
    $inventoryHashBefore = (Get-FileHash -LiteralPath $inventoryPath -Algorithm SHA256).Hash
    $snapshotHashBefore = (Get-FileHash -LiteralPath $snapshotPath -Algorithm SHA256).Hash
    New-VcfFleetArchitecture -InventoryPath $inventoryPath -CompatibilitySnapshotPath $snapshotPath -OutputDirectory $tempOutput
    Assert-Value -Actual (Get-FileHash -LiteralPath $inventoryPath -Algorithm SHA256).Hash -Expected $inventoryHashBefore -Label 'Inventory input integrity'
    Assert-Value -Actual (Get-FileHash -LiteralPath $snapshotPath -Algorithm SHA256).Hash -Expected $snapshotHashBefore -Label 'Compatibility snapshot input integrity'
    $generatedSddc = Join-Path $tempOutput 'sddc-spec.json'
    $generatedPlan = Join-Path $tempOutput 'migration-plan.json'
    Assert-SddcSpecSchema -CandidatePath $generatedSddc
    Assert-PlanSchema -CandidatePath $generatedPlan
    Assert-Value -Actual (Get-CanonicalJson -Path $generatedSddc) -Expected (Get-CanonicalJson -Path $sddcPath) -Label 'Module-generated SddcSpec'
    Assert-Value -Actual (Get-CanonicalJson -Path $generatedPlan) -Expected (Get-CanonicalJson -Path $planPath) -Label 'Module-generated migration plan'
}
finally {
    if (Test-Path -LiteralPath $tempOutput) {
        Remove-Item -LiteralPath $tempOutput -Recurse -Force
    }
}

Write-Host 'PASS: VCF architecture artifacts, migration gates, Edge sizing, uplinks, and module output are valid'
