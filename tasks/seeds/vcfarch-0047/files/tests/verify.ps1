$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
$artifactPath = Join-Path $root 'artifacts/vcf-architecture.json'
$openApiPath = Join-Path $root 'specifications/vcf-installer/vcf-installer-openapi.json'
$requirementsPath = Join-Path $root 'requirements/greenfield-requirements.json'
$inventoryPath = Join-Path $root 'fixtures/estate-inventory.json'
$snapshotPath = Join-Path $root 'fixtures/compatibility-snapshot.json'
$migrationSchemaPath = Join-Path $root 'schemas/migration-plan.schema.json'
$researchPath = Join-Path $root 'research/consulted-sources.json'
$manifestPath = Join-Path $root 'src/VcfArchitecture/VcfArchitecture.psd1'
$modulePath = Join-Path $root 'src/VcfArchitecture/VcfArchitecture.psm1'

function Assert-True {
    param(
        [Parameter(Mandatory)] [bool] $Condition,
        [Parameter(Mandatory)] [string] $Message
    )

    if (-not $Condition) {
        throw "ASSERTION FAILED: $Message"
    }
}

function Assert-Equal {
    param(
        $Actual,
        $Expected,
        [Parameter(Mandatory)] [string] $Message
    )

    if ($Actual -cne $Expected) {
        throw "ASSERTION FAILED: $Message (expected '$Expected', got '$Actual')"
    }
}

function Assert-SetEqual {
    param(
        [object[]] $Actual,
        [object[]] $Expected,
        [Parameter(Mandatory)] [string] $Message
    )

    $left = @($Actual | ForEach-Object { [string]$_ } | Sort-Object -Unique)
    $right = @($Expected | ForEach-Object { [string]$_ } | Sort-Object -Unique)
    $difference = @(Compare-Object -ReferenceObject $right -DifferenceObject $left)
    if ($difference.Count -ne 0) {
        throw "ASSERTION FAILED: $Message (expected [$($right -join ', ')], got [$($left -join ', ')])"
    }
}

function Read-Json {
    param([Parameter(Mandatory)] [string] $Path)
    Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -Depth 100
}

function Assert-PasswordPlaceholders {
    param(
        $Value,
        [string] $Path = '$'
    )

    if ($null -eq $Value) {
        return
    }
    if ($Value -is [pscustomobject]) {
        foreach ($property in $Value.PSObject.Properties) {
            $propertyPath = "$Path.$($property.Name)"
            if ($property.Name -match '(?i)password$' -and -not [string]::IsNullOrWhiteSpace([string]$property.Value)) {
                Assert-True ([string]$property.Value -match '^\$\{[A-Z][A-Z0-9_]*\}$') "$propertyPath must be a deployment placeholder"
            }
            Assert-PasswordPlaceholders -Value $property.Value -Path $propertyPath
        }
        return
    }
    if ($Value -is [System.Array]) {
        for ($index = 0; $index -lt $Value.Count; $index++) {
            Assert-PasswordPlaceholders -Value $Value[$index] -Path "$Path[$index]"
        }
    }
}

# Phase 1 is intentionally the installer-schema gate. No design, fixture,
# compatibility, module, or migration assertions run before SddcSpec validation.
Assert-True (Test-Path -LiteralPath $artifactPath -PathType Leaf) 'architecture artifact is missing'
$artifact = Read-Json $artifactPath
Assert-True ($null -ne $artifact.greenfield.sddcSpec) 'greenfield.sddcSpec is missing'
Assert-True (Test-Path -LiteralPath $openApiPath -PathType Leaf) 'pinned installer OpenAPI document is missing'
$openApi = Read-Json $openApiPath

$installerSchema = [ordered]@{
    '$schema' = 'http://json-schema.org/draft-07/schema#'
    '$ref' = '#/components/schemas/SddcSpec'
    components = $openApi.components
} | ConvertTo-Json -Depth 100 -Compress
$sddcJson = $artifact.greenfield.sddcSpec | ConvertTo-Json -Depth 100 -Compress
$schemaErrors = @()
$schemaValid = Test-Json -Json $sddcJson -Schema $installerSchema -ErrorVariable schemaErrors -ErrorAction SilentlyContinue
if (-not $schemaValid) {
    $details = ($schemaErrors | ForEach-Object { $_.ToString() }) -join '; '
    throw "INSTALLER SCHEMA FAILED: greenfield.sddcSpec does not validate as components.schemas.SddcSpec from the pinned 9.0.0.0 installer specification. $details"
}
Write-Host 'PASS installer-schema-first'

# Phase 2: deterministic architecture checks use only the artifact and protected
# local requirements, inventory, schemas, and compatibility snapshot.
$requirements = Read-Json $requirementsPath
$inventory = Read-Json $inventoryPath
$snapshot = Read-Json $snapshotPath

Assert-True (Test-Path -LiteralPath $researchPath -PathType Leaf) 'consulted-source record is missing'
$researchRaw = Get-Content -LiteralPath $researchPath -Raw
Assert-True ($researchRaw -match '^\s*\[') 'consulted-source record must be a JSON array'
$researchSources = @(Read-Json $researchPath)
Assert-True ($researchSources.Count -gt 0) 'consulted-source record must contain at least one source'
foreach ($source in $researchSources) {
    $propertyNames = @($source.PSObject.Properties.Name)
    foreach ($requiredProperty in @('title', 'url', 'retrievedAt', 'informedDecisions')) {
        Assert-True ($propertyNames -ccontains $requiredProperty) "consulted source is missing $requiredProperty"
    }
    Assert-True ($source.title -is [string] -and -not [string]::IsNullOrWhiteSpace($source.title)) 'consulted source title must be a nonempty string'
    Assert-True ($source.url -is [string] -and -not [string]::IsNullOrWhiteSpace($source.url)) 'consulted source URL must be a nonempty string'
    Assert-True ($source.retrievedAt -is [string] -and -not [string]::IsNullOrWhiteSpace($source.retrievedAt)) 'consulted source retrieval time must be a nonempty string'
    $consultedUri = $null
    Assert-True ([uri]::TryCreate([string]$source.url, [UriKind]::Absolute, [ref]$consultedUri)) 'consulted source URL is not absolute'
    Assert-True (@('http', 'https') -ccontains $consultedUri.Scheme) 'consulted source URL is not HTTP(S)'
    Assert-True ($consultedUri.Host -match '(^|\.)broadcom\.com$|(^|\.)vmware\.com$') 'consulted source is not Broadcom-published material'
    Assert-True ($source.informedDecisions -is [System.Array]) 'consulted source informedDecisions must be an array'
    $decisions = @($source.informedDecisions)
    Assert-True ($decisions.Count -gt 0) 'consulted source does not identify an informed decision'
    foreach ($decision in $decisions) {
        Assert-True ($decision -is [string] -and -not [string]::IsNullOrWhiteSpace($decision)) 'consulted source has an informed decision that is not a nonempty string'
    }
}

Assert-Equal $artifact.artifactVersion '1.0' 'artifact version'
Assert-Equal $artifact.greenfield.requirementsId $requirements.requirementsId 'greenfield requirements identity'
Assert-Equal ([int]$artifact.greenfield.capacity.usableCpuCores) ([int]$requirements.capacity.usableCpuCores) 'usable CPU capacity'
Assert-Equal ([int]$artifact.greenfield.capacity.usableMemoryGiB) ([int]$requirements.capacity.usableMemoryGiB) 'usable memory capacity'
Assert-Equal ([int]$artifact.greenfield.capacity.usableStorageTiB) ([int]$requirements.capacity.usableStorageTiB) 'usable storage capacity'
Assert-Equal ([int]$artifact.greenfield.capacity.freeSpacePercent) ([int]$requirements.capacity.freeSpacePercent) 'free-space capacity requirement'
Assert-Equal $artifact.greenfield.sddcSpec.sddcId $requirements.sddc.sddcId 'SDDC id'
Assert-Equal $artifact.greenfield.sddcSpec.workflowType $requirements.sddc.workflowType 'workflow type'
Assert-Equal $artifact.greenfield.sddcSpec.version $requirements.targetVcfVersion 'SDDC version'
Assert-Equal $artifact.greenfield.sddcSpec.vcfInstanceName $requirements.sddc.instanceName 'VCF instance name'

$eligibleOptions = @($snapshot.greenfieldOptions | Where-Object {
    $_.supported -eq $true -and $_.supportedHostProfileIds -contains $requirements.hostProfile.profileId
})
Assert-True ($eligibleOptions.Count -ge 2) 'snapshot must expose both supported storage alternatives'
$selectedOption = $eligibleOptions | Sort-Object -Property selectionRank, hostCount | Select-Object -First 1
Assert-Equal $artifact.greenfield.storageDecision.objective $requirements.selectionObjective 'storage selection objective'
Assert-Equal $artifact.greenfield.storageDecision.selectedArchitecture $selectedOption.architecture 'selected storage architecture'
Assert-Equal ([int]$artifact.greenfield.storageDecision.hostCount) ([int]$selectedOption.hostCount) 'selected host count'
Assert-Equal ([int]$artifact.greenfield.storageDecision.minimumPhysicalNicGbps) ([int]$selectedOption.minimumPhysicalNicGbps) 'selected physical NIC speed'

$artifactAlternatives = @($artifact.greenfield.storageDecision.alternatives)
Assert-Equal $artifactAlternatives.Count $eligibleOptions.Count 'storage alternative count'
foreach ($option in $eligibleOptions) {
    $alternative = @($artifactAlternatives | Where-Object architecture -ceq $option.architecture)
    Assert-Equal $alternative.Count 1 "one $($option.architecture) alternative"
    Assert-Equal ([int]$alternative[0].hostCount) ([int]$option.hostCount) "$($option.architecture) host count"
    Assert-Equal ([int]$alternative[0].minimumPhysicalNicGbps) ([int]$option.minimumPhysicalNicGbps) "$($option.architecture) NIC speed"
    Assert-Equal ([int]$alternative[0].mtu) ([int]$option.minimumMtu) "$($option.architecture) MTU"
}

$dataSites = @($requirements.sites | Where-Object role -ceq 'DATA')
$witnessSite = @($requirements.sites | Where-Object role -ceq 'WITNESS')
Assert-Equal $dataSites.Count 2 'two data sites are required'
Assert-Equal $witnessSite.Count 1 'one witness site is required'
Assert-Equal $artifact.greenfield.siteTopology.topology $requirements.availability.topology 'site topology'
Assert-Equal $artifact.greenfield.siteTopology.witness.siteId $witnessSite[0].siteId 'witness site'
Assert-Equal $artifact.greenfield.siteTopology.witness.hostname $witnessSite[0].witnessHostname 'witness hostname'

$placedHosts = @()
foreach ($site in $dataSites) {
    $placement = @($artifact.greenfield.siteTopology.dataSites | Where-Object siteId -ceq $site.siteId)
    Assert-Equal $placement.Count 1 "one placement for $($site.siteId)"
    Assert-Equal @($placement[0].hostnames).Count ([int]$selectedOption.dataHostsPerSite) "equal host placement for $($site.siteId)"
    $availableAtSite = @($requirements.availableHosts | Where-Object siteId -ceq $site.siteId | ForEach-Object hostname)
    foreach ($hostname in @($placement[0].hostnames)) {
        Assert-True ($availableAtSite -ccontains $hostname) "$hostname belongs to $($site.siteId)"
        $placedHosts += $hostname
    }
}
Assert-Equal $placedHosts.Count ([int]$selectedOption.hostCount) 'total placed hosts'
Assert-Equal @($placedHosts | Sort-Object -Unique).Count $placedHosts.Count 'host placement has no duplicates'
$specHosts = @($artifact.greenfield.sddcSpec.hostSpecs | ForEach-Object hostname)
Assert-SetEqual $specHosts $placedHosts 'SddcSpec hosts match site placement'

$sddcSpec = $artifact.greenfield.sddcSpec
Assert-PasswordPlaceholders -Value $sddcSpec -Path '$.greenfield.sddcSpec'
Assert-Equal $sddcSpec.dnsSpec.subdomain $requirements.sddc.dnsSubdomain 'DNS subdomain'
Assert-SetEqual @($sddcSpec.dnsSpec.nameservers) @($requirements.sddc.nameServers) 'DNS servers'
Assert-SetEqual @($sddcSpec.ntpServers) @($requirements.sddc.ntpServers) 'NTP servers'
Assert-Equal $sddcSpec.vcenterSpec.vcenterHostname $requirements.sddc.vcenterHostname 'vCenter hostname'
Assert-True ([string]$sddcSpec.vcenterSpec.rootVcenterPassword -match '^\$\{[A-Z0-9_]+\}$') 'vCenter password must be a deployment placeholder'
Assert-Equal $sddcSpec.clusterSpec.datacenterName $requirements.sddc.datacenterName 'datacenter name'
Assert-Equal $sddcSpec.clusterSpec.clusterName $requirements.sddc.clusterName 'cluster name'
Assert-Equal $sddcSpec.clusterSpec.clusterEvcMode $requirements.hostProfile.cpuGeneration 'EVC mode'
Assert-True ($sddcSpec.datastoreSpec.vsanSpec.esaConfig.enabled -eq ($selectedOption.architecture -ceq 'ESA')) 'SddcSpec ESA flag follows the decision'
Assert-Equal ([int]$sddcSpec.datastoreSpec.vsanSpec.failuresToTolerate) ([int]$requirements.availability.hostFailuresToTolerate) 'vSAN FTT'

$dvs = @($sddcSpec.dvsSpecs)
Assert-Equal $dvs.Count 1 'one distributed switch'
Assert-Equal $dvs[0].dvsName $requirements.network.distributedSwitchName 'distributed switch name'
Assert-Equal ([int]$dvs[0].mtu) ([int]$requirements.network.mtu) 'distributed switch MTU'
Assert-SetEqual @($dvs[0].networks) @($requirements.network.networks | ForEach-Object networkType) 'distributed switch traffic types'
Assert-Equal @($dvs[0].vmnicsToUplinks).Count ([int]$requirements.hostProfile.physicalNicsPerHost) 'redundant physical uplink count'

$specNetworks = @($sddcSpec.networkSpecs)
Assert-Equal $specNetworks.Count @($requirements.network.networks).Count 'network count'
foreach ($requiredNetwork in @($requirements.network.networks)) {
    $actualNetwork = @($specNetworks | Where-Object networkType -ceq $requiredNetwork.networkType)
    Assert-Equal $actualNetwork.Count 1 "one $($requiredNetwork.networkType) network"
    Assert-Equal ([int]$actualNetwork[0].vlanId) ([int]$requiredNetwork.vlanId) "$($requiredNetwork.networkType) VLAN"
    Assert-Equal $actualNetwork[0].subnet $requiredNetwork.subnet "$($requiredNetwork.networkType) subnet"
    Assert-Equal $actualNetwork[0].gateway $requiredNetwork.gateway "$($requiredNetwork.networkType) gateway"
    Assert-Equal ([int]$actualNetwork[0].mtu) ([int]$requirements.network.mtu) "$($requiredNetwork.networkType) MTU"
    Assert-Equal $actualNetwork[0].includeIpAddressRanges[0].startIpAddress $requiredNetwork.startIpAddress "$($requiredNetwork.networkType) start address"
    Assert-Equal $actualNetwork[0].includeIpAddressRanges[0].endIpAddress $requiredNetwork.endIpAddress "$($requiredNetwork.networkType) end address"
}

$migrationPlan = $artifact.existingEstate.migrationPlan
$migrationJson = $migrationPlan | ConvertTo-Json -Depth 100 -Compress
$migrationErrors = @()
$migrationValid = Test-Json -Json $migrationJson -SchemaFile $migrationSchemaPath -ErrorVariable migrationErrors -ErrorAction SilentlyContinue
if (-not $migrationValid) {
    $details = ($migrationErrors | ForEach-Object { $_.ToString() }) -join '; '
    throw "MIGRATION SCHEMA FAILED: $details"
}
Assert-Equal $migrationPlan.estateId $inventory.estateId 'migration estate identity'
Assert-Equal $migrationPlan.targetVcfVersion $snapshot.targetVcfVersion 'migration target VCF version'
Assert-Equal $migrationPlan.storageTransition.source $snapshot.storageTransition.source 'storage transition source'
Assert-Equal $migrationPlan.storageTransition.target $snapshot.storageTransition.target 'storage transition target'
Assert-Equal $migrationPlan.storageTransition.inPlace ([bool]$snapshot.storageTransition.inPlaceSupported) 'storage transition in-place flag'
Assert-Equal $migrationPlan.storageTransition.mode $snapshot.storageTransition.mode 'storage transition mode'

$inventoryComponents = @($inventory.components)
$upgradePlan = @($snapshot.upgradePlan)
$steps = @($migrationPlan.steps)
Assert-Equal $steps.Count $inventoryComponents.Count 'one migration step per inventory component'
Assert-Equal $upgradePlan.Count $inventoryComponents.Count 'snapshot covers every inventory component'
Assert-SetEqual @($steps | ForEach-Object componentId) @($inventoryComponents | ForEach-Object componentId) 'migration steps cover inventory exactly'
Assert-SetEqual @($upgradePlan | ForEach-Object componentId) @($inventoryComponents | ForEach-Object componentId) 'snapshot upgrade plan covers inventory exactly'

foreach ($component in $inventoryComponents) {
    $step = @($steps | Where-Object componentId -ceq $component.componentId)
    $authority = @($upgradePlan | Where-Object componentId -ceq $component.componentId)
    Assert-Equal $step.Count 1 "one migration step for $($component.componentId)"
    Assert-Equal $authority.Count 1 "one pinned target for $($component.componentId)"
    Assert-Equal $step[0].name $component.name "$($component.componentId) name"
    Assert-Equal $step[0].fromVersion $component.currentVersion "$($component.componentId) current version"
    Assert-Equal $step[0].targetVersion $authority[0].targetVersion "$($component.componentId) target version"
    Assert-Equal $step[0].action $authority[0].action "$($component.componentId) action"
    Assert-Equal ([int]$step[0].order) ([int]$authority[0].order) "$($component.componentId) order"
    Assert-SetEqual @($step[0].gates | ForEach-Object gateId) @($authority[0].requiredGateIds) "$($component.componentId) compatibility gates"
    foreach ($gate in @($step[0].gates)) {
        $expectedCondition = $snapshot.gateCatalog.($gate.gateId)
        Assert-True (-not [string]::IsNullOrWhiteSpace([string]$expectedCondition)) "known gate $($gate.gateId)"
        Assert-Equal $gate.condition $expectedCondition "$($gate.gateId) condition"
    }
}
$orders = @($steps | Sort-Object order | ForEach-Object { [int]$_.order })
Assert-SetEqual $orders @(1..$steps.Count) 'migration order is contiguous'

$manifest = Import-PowerShellDataFile -LiteralPath $manifestPath
$requiredModuleNames = @($manifest.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ } else { $_.ModuleName }
})
Assert-True ($requiredModuleNames -ccontains 'VMware.Sdk.Vcf.Installer') 'module manifest requires VMware.Sdk.Vcf.Installer'
$exportedFunctionNames = @($manifest.FunctionsToExport | ForEach-Object { [string]$_ })
Assert-True ($exportedFunctionNames -ccontains 'New-VcfArchitecture') 'module manifest exports New-VcfArchitecture'
Assert-True ($exportedFunctionNames -ccontains 'Invoke-VcfArchitectureInstallerValidation') 'module manifest exports Invoke-VcfArchitectureInstallerValidation'
Assert-True (Test-Path -LiteralPath $modulePath -PathType Leaf) 'PowerShell module implementation is missing'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($modulePath, [ref]$tokens, [ref]$parseErrors)
Assert-Equal @($parseErrors).Count 0 'PowerShell module parses cleanly'
$functionNames = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) | ForEach-Object Name)
Assert-True ($functionNames -ccontains 'New-VcfArchitecture') 'New-VcfArchitecture is implemented'
Assert-True ($functionNames -ccontains 'Invoke-VcfArchitectureInstallerValidation') 'Invoke-VcfArchitectureInstallerValidation is implemented'
$validationFunction = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -ceq 'Invoke-VcfArchitectureInstallerValidation'
}, $true))
Assert-Equal $validationFunction.Count 1 'one installer-validation function is implemented'
$validationCommands = @($validationFunction[0].FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
}, $true))
$requiredSdkParameters = [ordered]@{
    'Initialize-VcfInstallerSddcHostSpec' = @('Hostname')
    'Initialize-VcfInstallerSddcVcenterSpec' = @(
        'VcenterHostname', 'RootVcenterPassword', 'VmSize', 'StorageSize', 'SsoDomain',
        'AdminUserSsoUsername', 'AdminUserSsoPassword', 'UseExistingDeployment', 'Version'
    )
    'Initialize-VcfInstallerSddcClusterSpec' = @('DatacenterName', 'ClusterName', 'ClusterEvcMode')
    'Initialize-VcfInstallerIpRange' = @('StartIpAddress', 'EndIpAddress')
    'Initialize-VcfInstallerSddcNetworkSpec' = @(
        'NetworkType', 'Subnet', 'Gateway', 'SubnetMask', 'IncludeIpAddressRanges', 'VlanId', 'Mtu'
    )
    'Initialize-VcfInstallerVmnicToUplink' = @('Id', 'Uplink')
    'Initialize-VcfInstallerDvsSpec' = @('DvsName', 'Networks', 'Mtu', 'VmnicsToUplinks')
    'Initialize-VcfInstallerDnsSpec' = @('Subdomain', 'Nameservers')
    'Initialize-VcfInstallerVsanEsaConfig' = @('Enabled')
    'Initialize-VcfInstallerVsanSpec' = @('DatastoreName', 'VsanDedup', 'EsaConfig', 'FailuresToTolerate')
    'Initialize-VcfInstallerSddcDatastoreSpec' = @('VsanSpec')
    'Initialize-VcfInstallerSddcSpec' = @(
        'SddcId', 'WorkflowType', 'HostSpecs', 'Version', 'VcenterSpec', 'ClusterSpec',
        'DvsSpecs', 'NetworkSpecs', 'DnsSpec', 'NtpServers', 'DatastoreSpec', 'CeipEnabled',
        'SkipEsxThumbprintValidation', 'SkipGatewayPingValidation', 'VcfInstanceName'
    )
    'Invoke-VcfInstallerValidateSddcSpec' = @('SddcSpec')
}
foreach ($sdkExpectation in $requiredSdkParameters.GetEnumerator()) {
    $commandInstances = @($validationCommands | Where-Object { $_.GetCommandName() -ceq $sdkExpectation.Key })
    Assert-Equal $commandInstances.Count 1 "one $($sdkExpectation.Key) call in installer validation"
    $actualParameterNames = @($commandInstances[0].CommandElements |
        Where-Object { $_ -is [System.Management.Automation.Language.CommandParameterAst] } |
        ForEach-Object ParameterName)
    foreach ($requiredParameterName in @($sdkExpectation.Value)) {
        Assert-True ($actualParameterNames -ccontains $requiredParameterName) "$($sdkExpectation.Key) supplies -$requiredParameterName"
    }
}

Import-Module -Name $modulePath -Force
$temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("vcfarch-0047-" + [guid]::NewGuid().ToString('N'))
try {
    $null = New-Item -ItemType Directory -Path $temporaryDirectory
    $generatedPath = Join-Path $temporaryDirectory 'vcf-architecture.json'
    $null = New-VcfArchitecture `
        -RequirementsPath $requirementsPath `
        -InventoryPath $inventoryPath `
        -CompatibilitySnapshotPath $snapshotPath `
        -OutputPath $generatedPath
    $generated = Read-Json $generatedPath
    $expectedCanonical = $artifact | ConvertTo-Json -Depth 100 -Compress
    $actualCanonical = $generated | ConvertTo-Json -Depth 100 -Compress
    Assert-Equal $actualCanonical $expectedCanonical 'module deterministically regenerates the committed artifact'

    # Exercise each declared input independently of the committed output so a
    # hard-coded artifact copier cannot satisfy the generator contract.
    $mutatedRequirements = Read-Json $requirementsPath
    $mutatedRequirements.requirementsId = 'mutation-requirements-id'
    $mutatedRequirements.capacity.usableCpuCores = 129
    $mutatedRequirements.network.networks[0].vlanId = 1611
    $mutatedRequirementsPath = Join-Path $temporaryDirectory 'requirements.json'
    $mutatedRequirements | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $mutatedRequirementsPath -Encoding utf8

    $mutatedInventory = Read-Json $inventoryPath
    $mutatedInventory.estateId = 'mutation-estate-id'
    $mutatedInventory.components[0].currentVersion = '8.18.0-mutation'
    $mutatedInventoryPath = Join-Path $temporaryDirectory 'inventory.json'
    $mutatedInventory | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $mutatedInventoryPath -Encoding utf8

    $mutatedSnapshot = Read-Json $snapshotPath
    $mutatedSnapshot.targetVcfVersion = '9.0.0.0-mutation'
    $mutatedSnapshot.gateCatalog.'upgrade-bundle-staged' = 'Mutation gate condition.'
    $mutatedSnapshotPath = Join-Path $temporaryDirectory 'snapshot.json'
    $mutatedSnapshot | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $mutatedSnapshotPath -Encoding utf8

    $mutatedOutputPath = Join-Path $temporaryDirectory 'mutated-vcf-architecture.json'
    $null = New-VcfArchitecture `
        -RequirementsPath $mutatedRequirementsPath `
        -InventoryPath $mutatedInventoryPath `
        -CompatibilitySnapshotPath $mutatedSnapshotPath `
        -OutputPath $mutatedOutputPath
    $mutated = Read-Json $mutatedOutputPath
    Assert-Equal $mutated.greenfield.requirementsId 'mutation-requirements-id' 'generator consumes requirements identity'
    Assert-Equal ([int]$mutated.greenfield.capacity.usableCpuCores) 129 'generator consumes requirements capacity'
    Assert-Equal ([int]$mutated.greenfield.sddcSpec.networkSpecs[0].vlanId) 1611 'generator consumes requirements network'
    Assert-Equal $mutated.existingEstate.migrationPlan.estateId 'mutation-estate-id' 'generator consumes inventory identity'
    $mutatedFirstStep = @($mutated.existingEstate.migrationPlan.steps | Where-Object componentId -ceq 'aria-suite-lifecycle')
    Assert-Equal $mutatedFirstStep.Count 1 'mutated inventory component remains represented once'
    Assert-Equal $mutatedFirstStep[0].fromVersion '8.18.0-mutation' 'generator consumes inventory component versions'
    Assert-Equal $mutated.existingEstate.migrationPlan.targetVcfVersion '9.0.0.0-mutation' 'generator consumes compatibility target'
    $mutatedUpgradeGate = @($mutatedFirstStep[0].gates | Where-Object gateId -ceq 'upgrade-bundle-staged')
    Assert-Equal $mutatedUpgradeGate.Count 1 'mutated compatibility gate remains represented once'
    Assert-Equal $mutatedUpgradeGate[0].condition 'Mutation gate condition.' 'generator consumes compatibility gate conditions'
}
finally {
    if (Test-Path -LiteralPath $temporaryDirectory) {
        Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
    }
}

Write-Host 'PASS deterministic-architecture'
