[CmdletBinding()]
param(
    [Parameter()]
    [string]$Root = $PSScriptRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-Condition {
    param(
        [Parameter(Mandatory)]
        [bool]$Condition,

        [Parameter(Mandatory)]
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Assert-SequenceEqual {
    param(
        [Parameter()]
        [AllowEmptyCollection()]
        [object[]]$Actual,

        [Parameter()]
        [AllowEmptyCollection()]
        [object[]]$Expected,

        [Parameter(Mandatory)]
        [string]$Message
    )

    $actualValues = @($Actual)
    $expectedValues = @($Expected)
    Assert-Condition ($actualValues.Count -eq $expectedValues.Count) "$Message (count differs)"
    for ($index = 0; $index -lt $expectedValues.Count; $index++) {
        Assert-Condition ($actualValues[$index] -ceq $expectedValues[$index]) "$Message (index $index differs)"
    }
}

function Assert-JsonEquivalent {
    param(
        [Parameter()][AllowNull()][object]$Actual,
        [Parameter()][AllowNull()][object]$Expected,
        [Parameter(Mandatory)][string]$Path
    )

    if ($null -eq $Actual -or $null -eq $Expected) {
        Assert-Condition ($null -eq $Actual -and $null -eq $Expected) "$Path differs"
        return
    }

    if ($Actual -is [pscustomobject] -or $Expected -is [pscustomobject]) {
        Assert-Condition ($Actual -is [pscustomobject] -and $Expected -is [pscustomobject]) "$Path JSON types differ"
        $actualNames = @($Actual.PSObject.Properties.Name | Sort-Object)
        $expectedNames = @($Expected.PSObject.Properties.Name | Sort-Object)
        Assert-SequenceEqual $actualNames $expectedNames "$Path properties differ"
        foreach ($name in $expectedNames) {
            Assert-JsonEquivalent $Actual.$name $Expected.$name "$Path.$name"
        }
        return
    }

    $actualIsArray = $Actual -is [System.Collections.IEnumerable] -and $Actual -isnot [string]
    $expectedIsArray = $Expected -is [System.Collections.IEnumerable] -and $Expected -isnot [string]
    if ($actualIsArray -or $expectedIsArray) {
        Assert-Condition ($actualIsArray -and $expectedIsArray) "$Path JSON types differ"
        $actualValues = @($Actual)
        $expectedValues = @($Expected)
        Assert-Condition ($actualValues.Count -eq $expectedValues.Count) "$Path array lengths differ"
        for ($index = 0; $index -lt $expectedValues.Count; $index++) {
            Assert-JsonEquivalent $actualValues[$index] $expectedValues[$index] "$Path[$index]"
        }
        return
    }

    Assert-Condition ($Actual -ceq $Expected) "$Path differs"
}

function Get-JsonDocument {
    param([Parameter(Mandatory)][string]$Path)

    Assert-Condition (Test-Path -LiteralPath $Path -PathType Leaf) "Missing required file: $Path"
    return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -Depth 100
}

function Test-DocumentSchema {
    param(
        [Parameter(Mandatory)][string]$DocumentPath,
        [Parameter(Mandatory)][string]$SchemaPath,
        [Parameter(Mandatory)][string]$Label
    )

    Assert-Condition (Test-Path -LiteralPath $DocumentPath -PathType Leaf) "Missing required file: $DocumentPath"
    Assert-Condition (Test-Path -LiteralPath $SchemaPath -PathType Leaf) "Missing required file: $SchemaPath"
    $valid = (Get-Content -LiteralPath $DocumentPath -Raw) | Test-Json -SchemaFile $SchemaPath -ErrorAction SilentlyContinue
    Assert-Condition $valid "$Label does not conform to its fixed schema"
}

$installerSchemaPath = Join-Path $Root 'specifications/vcf-installer/vcf-installer-openapi.json'
$sddcSpecPath = Join-Path $Root 'artifacts/greenfield-sddc-spec.json'

# This is intentionally the first artifact check. The root wrapper selects the
# unmodified SddcSpec component while retaining the tagged document's own refs.
Assert-Condition (Test-Path -LiteralPath $installerSchemaPath -PathType Leaf) "Missing pinned installer specification"
Assert-Condition (Test-Path -LiteralPath $sddcSpecPath -PathType Leaf) "Missing greenfield SddcSpec artifact"
$installerOpenApi = Get-Content -LiteralPath $installerSchemaPath -Raw | ConvertFrom-Json -Depth 100
$sddcValidationSchema = [ordered]@{
    '$schema' = 'http://json-schema.org/draft-07/schema#'
    '$ref' = '#/components/schemas/SddcSpec'
    components = $installerOpenApi.components
} | ConvertTo-Json -Depth 100
$sddcSchemaValid = (Get-Content -LiteralPath $sddcSpecPath -Raw) | Test-Json -Schema $sddcValidationSchema -ErrorAction SilentlyContinue
Assert-Condition $sddcSchemaValid 'greenfield-sddc-spec.json does not validate as the tagged installer SddcSpec'
Write-Host 'PASS 1: installer SddcSpec schema validation'

$expectedInstallerHash = '29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d'
$installerHash = (Get-FileHash -LiteralPath $installerSchemaPath -Algorithm SHA256).Hash.ToLowerInvariant()
Assert-Condition ($installerHash -ceq $expectedInstallerHash) 'Pinned installer specification has changed'
Assert-Condition ($installerOpenApi.info.version -ceq '9.1.0.0') 'Installer specification is not version 9.1.0.0'

$requirementsPath = Join-Path $Root 'inputs/design-requirements.json'
$inventoryPath = Join-Path $Root 'fixtures/estate-inventory.json'
$snapshotPath = Join-Path $Root 'compatibility/compatibility-snapshot.json'
$architecturePath = Join-Path $Root 'artifacts/architecture.json'
$migrationPath = Join-Path $Root 'artifacts/migration-plan.json'
$researchPath = Join-Path $Root 'artifacts/research-sources.json'
$architectureSchemaPath = Join-Path $Root 'schemas/architecture.schema.json'
$migrationSchemaPath = Join-Path $Root 'schemas/migration-plan.schema.json'

Test-DocumentSchema -DocumentPath $architecturePath -SchemaPath $architectureSchemaPath -Label 'architecture.json'
Test-DocumentSchema -DocumentPath $migrationPath -SchemaPath $migrationSchemaPath -Label 'migration-plan.json'

$requirements = Get-JsonDocument $requirementsPath
$inventory = Get-JsonDocument $inventoryPath
$snapshot = Get-JsonDocument $snapshotPath
$sddcSpec = Get-JsonDocument $sddcSpecPath
$architecture = Get-JsonDocument $architecturePath
$migration = Get-JsonDocument $migrationPath
$research = Get-JsonDocument $researchPath

Assert-Condition ($requirements.targetRelease -ceq $snapshot.targetRelease) 'Requirement and snapshot target releases differ'
Assert-Condition ($sddcSpec.version -ceq $requirements.targetRelease) 'SddcSpec target release is incorrect'
Assert-Condition ($sddcSpec.workflowType -ceq $snapshot.greenfield.workflowType) 'SddcSpec workflowType is incorrect'
Assert-Condition ($sddcSpec.sddcId -ceq $requirements.managementDomainName) 'SddcSpec management domain ID is incorrect'
Assert-Condition ($sddcSpec.vcfInstanceName -ceq $requirements.vcfInstanceName) 'SddcSpec VCF instance name is incorrect'
Assert-Condition ($sddcSpec.vcenterSpec.vcenterHostname -ceq $requirements.componentFqdns.vcenter) 'vCenter FQDN is incorrect'
Assert-Condition ($sddcSpec.vcenterSpec.version -ceq $requirements.targetRelease) 'vCenter target release is incorrect'
Assert-Condition (-not [bool]$sddcSpec.vcenterSpec.useExistingDeployment) 'vCenter must be a greenfield deployment'
Assert-Condition ($sddcSpec.sddcManagerSpec.hostname -ceq $requirements.componentFqdns.sddcManager) 'SDDC Manager FQDN is incorrect'
Assert-Condition ($sddcSpec.sddcManagerSpec.version -ceq $requirements.targetRelease) 'SDDC Manager target release is incorrect'
Assert-Condition (-not [bool]$sddcSpec.sddcManagerSpec.useExistingDeployment) 'SDDC Manager must be a greenfield deployment'
Assert-Condition ($sddcSpec.nsxtSpec.vipFqdn -ceq $requirements.componentFqdns.nsxVip) 'NSX VIP FQDN is incorrect'
Assert-Condition ($sddcSpec.nsxtSpec.version -ceq $requirements.targetRelease) 'NSX target release is incorrect'
Assert-Condition (-not [bool]$sddcSpec.nsxtSpec.useExistingDeployment) 'NSX must be a greenfield deployment'
Assert-SequenceEqual @($sddcSpec.nsxtSpec.nsxtManagers.hostname | Sort-Object) @($requirements.componentFqdns.nsxManagers | Sort-Object) 'NSX manager FQDNs differ from requirements'
Assert-Condition ($sddcSpec.licenseServerSpec.hostname -ceq $requirements.componentFqdns.licenseServer) 'License Server FQDN is incorrect'
Assert-Condition ($sddcSpec.licenseServerSpec.version -ceq $requirements.targetRelease) 'License Server target release is incorrect'
Assert-Condition (-not [bool]$sddcSpec.licenseServerSpec.useExistingDeployment) 'License Server must be a greenfield deployment'
Assert-SequenceEqual @($sddcSpec.dnsSpec.nameservers) @($requirements.dns.nameServers) 'DNS servers differ from requirements'
Assert-SequenceEqual @($sddcSpec.ntpServers) @($requirements.dns.ntpServers) 'NTP servers differ from requirements'

$requiredDataSites = @($requirements.sites | Where-Object role -CEQ 'DATA')
$requiredWitnessSite = @($requirements.sites | Where-Object role -CEQ 'WITNESS')
Assert-Condition ($requiredDataSites.Count -eq $snapshot.greenfield.dataSiteCount) 'Fixture has an unexpected number of data sites'
Assert-Condition ($requiredWitnessSite.Count -eq 1) 'Fixture must have exactly one witness site'
$requiredHosts = @($requiredDataSites | ForEach-Object hosts)
$sddcHosts = @($sddcSpec.hostSpecs | ForEach-Object hostname)
Assert-SequenceEqual @($sddcHosts | Sort-Object) @($requiredHosts | Sort-Object) 'SddcSpec data hosts do not match the two site requirements'
Assert-Condition ($sddcHosts.Count -eq [int]$snapshot.greenfield.totalDataHosts) 'SddcSpec data host count is incorrect'
Assert-Condition ($sddcHosts -cnotcontains $requirements.witness.hostname) 'The witness must not be an installer data host'

$requiredNetworkTypes = @($snapshot.greenfield.requiredNetworkTypes)
Assert-SequenceEqual @($sddcSpec.networkSpecs.networkType | Sort-Object) @($requiredNetworkTypes | Sort-Object) 'SddcSpec network types are incomplete'
foreach ($requiredNetwork in $requirements.networks) {
    $actualNetworks = @($sddcSpec.networkSpecs | Where-Object networkType -CEQ $requiredNetwork.type)
    Assert-Condition ($actualNetworks.Count -eq 1) "Expected one installer network of type $($requiredNetwork.type)"
    $actualNetwork = $actualNetworks[0]
    Assert-Condition ([int]$actualNetwork.vlanId -eq [int]$requiredNetwork.vlanId) "VLAN differs for $($requiredNetwork.type)"
    Assert-Condition ($actualNetwork.subnet -ceq $requiredNetwork.subnet) "Subnet differs for $($requiredNetwork.type)"
    Assert-Condition ($actualNetwork.gateway -ceq $requiredNetwork.gateway) "Gateway differs for $($requiredNetwork.type)"
    Assert-Condition ([int]$actualNetwork.mtu -eq [int]$requiredNetwork.mtu) "MTU differs for $($requiredNetwork.type)"
    Assert-Condition (@($actualNetwork.includeIpAddressRanges).Count -eq 1) "IP range is missing for $($requiredNetwork.type)"
    Assert-Condition ($actualNetwork.includeIpAddressRanges[0].startIpAddress -ceq $requiredNetwork.poolStart) "Pool start differs for $($requiredNetwork.type)"
    Assert-Condition ($actualNetwork.includeIpAddressRanges[0].endIpAddress -ceq $requiredNetwork.poolEnd) "Pool end differs for $($requiredNetwork.type)"
}
Assert-Condition ([int]$sddcSpec.datastoreSpec.vsanSpec.failuresToTolerate -eq [int]$snapshot.greenfield.failuresToTolerate) 'vSAN failuresToTolerate is incorrect'
Write-Host 'PASS 2: greenfield SddcSpec semantics'

Assert-Condition ($architecture.designId -ceq $requirements.designId) 'Architecture design ID is incorrect'
Assert-Condition ($architecture.targetRelease -ceq $requirements.targetRelease) 'Architecture release is incorrect'
Assert-Condition (@($architecture.sites).Count -eq @($requirements.sites).Count) 'Architecture site count is incorrect'
Assert-Condition (@($architecture.sites.siteId | Sort-Object -Unique).Count -eq @($architecture.sites).Count) 'Architecture site IDs are not unique'
foreach ($requiredSite in $requirements.sites) {
    $actualSites = @($architecture.sites | Where-Object siteId -CEQ $requiredSite.id)
    Assert-Condition ($actualSites.Count -eq 1) "Architecture site $($requiredSite.id) is missing or duplicated"
    Assert-Condition ($actualSites[0].role -ceq $requiredSite.role) "Architecture role differs for $($requiredSite.id)"
    Assert-Condition ($actualSites[0].location -ceq $requiredSite.location) "Architecture location differs for $($requiredSite.id)"
    Assert-SequenceEqual @($actualSites[0].hosts) @($requiredSite.hosts) "Architecture hosts differ for $($requiredSite.id)"
}
Assert-Condition ($architecture.managementDomain.name -ceq $requirements.managementDomainName) 'Architecture management domain name is incorrect'
Assert-Condition ([bool]$architecture.managementDomain.stretched) 'Management domain is not stretched'
Assert-SequenceEqual @($architecture.managementDomain.dataSites | Sort-Object) @($requiredDataSites.id | Sort-Object) 'Architecture data sites are incorrect'
Assert-Condition ($architecture.managementDomain.preferredDataSite -ceq $requirements.availability.preferredDataSite) 'Preferred site is incorrect'

$architectureHosts = @($architecture.managementDomain.dataHosts)
Assert-SequenceEqual @($architectureHosts.hostname | Sort-Object) @($requiredHosts | Sort-Object) 'Architecture host inventory is incorrect'
foreach ($requiredSite in $requiredDataSites) {
    $siteHosts = @($architectureHosts | Where-Object siteId -CEQ $requiredSite.id)
    Assert-Condition ($siteHosts.Count -eq [int]$requirements.capacity.dataHostsPerSite) "Incorrect host count at $($requiredSite.id)"
    Assert-SequenceEqual @($siteHosts.hostname | Sort-Object) @($requiredSite.hosts | Sort-Object) "Incorrect host assignment at $($requiredSite.id)"
}
foreach ($hostModel in $architectureHosts) {
    Assert-Condition ([int]$hostModel.cpuCores -eq [int]$requirements.capacity.cpuCoresPerHost) "Incorrect CPU sizing for $($hostModel.hostname)"
    Assert-Condition ([int]$hostModel.memoryGiB -eq [int]$requirements.capacity.memoryGiBPerHost) "Incorrect memory sizing for $($hostModel.hostname)"
    Assert-Condition ([double]$hostModel.rawStorageTiB -eq [double]$requirements.capacity.rawStorageTiBPerHost) "Incorrect storage sizing for $($hostModel.hostname)"
}

$expectedClusterCpu = [int]$requirements.capacity.dataHostCount * [int]$requirements.capacity.cpuCoresPerHost
$expectedClusterMemory = [int]$requirements.capacity.dataHostCount * [int]$requirements.capacity.memoryGiBPerHost
$expectedClusterStorage = [double]$requirements.capacity.dataHostCount * [double]$requirements.capacity.rawStorageTiBPerHost
$expectedSiteCpu = [int]$requirements.capacity.dataHostsPerSite * [int]$requirements.capacity.cpuCoresPerHost
$expectedSiteMemory = [int]$requirements.capacity.dataHostsPerSite * [int]$requirements.capacity.memoryGiBPerHost
$expectedSiteStorage = [double]$requirements.capacity.dataHostsPerSite * [double]$requirements.capacity.rawStorageTiBPerHost
Assert-Condition ([int]$architecture.capacity.clusterCpuCores -eq $expectedClusterCpu) 'Cluster CPU capacity is incorrect'
Assert-Condition ([int]$architecture.capacity.clusterMemoryGiB -eq $expectedClusterMemory) 'Cluster memory capacity is incorrect'
Assert-Condition ([double]$architecture.capacity.clusterRawStorageTiB -eq $expectedClusterStorage) 'Cluster raw storage capacity is incorrect'
Assert-Condition ([int]$architecture.capacity.survivingSiteCpuCores -eq $expectedSiteCpu) 'Surviving-site CPU capacity is incorrect'
Assert-Condition ([int]$architecture.capacity.survivingSiteMemoryGiB -eq $expectedSiteMemory) 'Surviving-site memory capacity is incorrect'
Assert-Condition ([double]$architecture.capacity.survivingSiteRawStorageTiB -eq $expectedSiteStorage) 'Surviving-site raw storage capacity is incorrect'
Assert-Condition ([int]$architecture.capacity.survivingSiteCpuCores -ge [int]$requirements.capacity.requiredSurvivingSiteCpuCores) 'Surviving-site CPU capacity is insufficient'
Assert-Condition ([int]$architecture.capacity.survivingSiteMemoryGiB -ge [int]$requirements.capacity.requiredSurvivingSiteMemoryGiB) 'Surviving-site memory capacity is insufficient'
Assert-Condition ([double]$architecture.capacity.designedUsableStorageTiB -ge [double]$requirements.capacity.requiredUsableStorageTiB) 'Designed usable storage is insufficient'

$witness = $architecture.managementDomain.witness
Assert-Condition ($witness.siteId -ceq $requirements.witness.siteId) 'Witness is not at the required third site'
Assert-Condition ($requiredDataSites.id -cnotcontains $witness.siteId) 'Witness shares a data-site failure domain'
Assert-Condition ($witness.hostname -ceq $requirements.witness.hostname) 'Witness hostname is incorrect'
Assert-Condition ($witness.address -ceq $requirements.witness.address) 'Witness address is incorrect'
Assert-Condition ($witness.faultDomain -ceq $snapshot.greenfield.witnessFaultDomain) 'Witness fault domain is incorrect'
Assert-Condition ([int]$witness.applianceCount -eq [int]$snapshot.greenfield.witnessCount) 'Witness appliance count is incorrect'
Assert-Condition ([bool]$witness.isDataHost -eq [bool]$snapshot.greenfield.witnessIsDataHost) 'Witness data-host role is incorrect'
Assert-Condition (-not [bool]$witness.includedInInstallerHostSpecs) 'Witness must be excluded from installer hostSpecs'
Assert-Condition ([int]$architecture.managementDomain.availability.dataSiteFailuresToTolerate -eq [int]$requirements.availability.dataSiteFailuresToTolerate) 'Site failure tolerance is incorrect'
Assert-Condition ([int]$architecture.managementDomain.availability.localHostFailuresToTolerate -eq [int]$requirements.availability.localHostFailuresToTolerate) 'Local host failure tolerance is incorrect'
Assert-Condition ([int]$architecture.managementDomain.availability.vsanFailuresToTolerate -eq [int]$snapshot.greenfield.failuresToTolerate) 'Architecture vSAN failure tolerance is incorrect'

Assert-SequenceEqual @($architecture.networks.type | Sort-Object) @($requirements.networks.type | Sort-Object) 'Architecture networks are incomplete'
foreach ($requiredNetwork in $requirements.networks) {
    $actual = @($architecture.networks | Where-Object type -CEQ $requiredNetwork.type)
    Assert-Condition ($actual.Count -eq 1) "Architecture network $($requiredNetwork.type) is not unique"
    Assert-Condition ([int]$actual[0].vlanId -eq [int]$requiredNetwork.vlanId) "Architecture VLAN differs for $($requiredNetwork.type)"
    Assert-Condition ($actual[0].subnet -ceq $requiredNetwork.subnet) "Architecture subnet differs for $($requiredNetwork.type)"
    Assert-Condition ($actual[0].gateway -ceq $requiredNetwork.gateway) "Architecture gateway differs for $($requiredNetwork.type)"
    Assert-Condition ([int]$actual[0].mtu -eq [int]$requiredNetwork.mtu) "Architecture MTU differs for $($requiredNetwork.type)"
    Assert-Condition ([bool]$actual[0].stretchedAcrossDataSites -eq [bool]$requiredNetwork.stretchedAcrossDataSites) "Architecture stretch flag differs for $($requiredNetwork.type)"
}
Write-Host 'PASS 3: stretched architecture, capacity, and witness placement'

Assert-Condition ($migration.estateId -ceq $inventory.estateId) 'Migration estate ID is incorrect'
Assert-Condition ($migration.sourceRelease -ceq $inventory.vcfRelease) 'Migration source release is incorrect'
Assert-Condition ($migration.targetRelease -ceq $snapshot.targetRelease) 'Migration target release is incorrect'
Assert-Condition (@($migration.steps).Count -eq @($inventory.components).Count) 'Migration must contain every inventory component exactly once'
Assert-Condition (@($migration.steps.componentId | Sort-Object -Unique).Count -eq @($migration.steps).Count) 'Migration component IDs are not unique'
Assert-Condition (@($migration.steps.order | Sort-Object -Unique).Count -eq @($migration.steps).Count) 'Migration order values are not unique'

$snapshotSteps = @($snapshot.migration | Sort-Object order)
Assert-Condition ($snapshotSteps.Count -eq @($inventory.components).Count) 'Pinned snapshot and estate inventory component counts differ'
for ($index = 0; $index -lt $snapshotSteps.Count; $index++) {
    $authority = $snapshotSteps[$index]
    $step = @($migration.steps | Where-Object componentId -CEQ $authority.componentId)
    $component = @($inventory.components | Where-Object componentId -CEQ $authority.componentId)
    Assert-Condition ($step.Count -eq 1) "Missing or duplicate migration step for $($authority.componentId)"
    Assert-Condition ($component.Count -eq 1) "Pinned component $($authority.componentId) is missing from inventory"
    $step = $step[0]
    $component = $component[0]
    Assert-Condition ([int]$step.order -eq [int]$authority.order) "Order is incorrect for $($authority.componentId)"
    Assert-Condition ($step.componentName -ceq $component.name) "Component name is incorrect for $($authority.componentId)"
    Assert-Condition ($step.currentVersion -ceq $component.currentVersion) "Current version is incorrect for $($authority.componentId)"
    Assert-Condition ($authority.supportedFrom -ccontains $step.currentVersion) "Source version is unsupported for $($authority.componentId)"
    Assert-Condition ($step.targetVersion -ceq $authority.targetVersion) "Target version is incorrect for $($authority.componentId)"
    Assert-Condition ($step.action -ceq $authority.action) "Action is incorrect for $($authority.componentId)"
    Assert-SequenceEqual @($step.gate.requiredPredecessors) @($authority.requiredPredecessors) "Gate predecessors differ for $($authority.componentId)"
    Assert-SequenceEqual @($step.gate.checks) @($authority.requiredChecks) "Gate checks differ for $($authority.componentId)"
    foreach ($predecessor in @($step.gate.requiredPredecessors)) {
        $predecessorStep = @($migration.steps | Where-Object componentId -CEQ $predecessor)
        Assert-Condition ($predecessorStep.Count -eq 1) "Unknown predecessor $predecessor"
        Assert-Condition ([int]$predecessorStep[0].order -lt [int]$step.order) "Predecessor $predecessor is not earlier than $($step.componentId)"
    }
}
Assert-SequenceEqual @($migration.steps.order | Sort-Object) @(1..$migration.steps.Count) 'Migration order is not contiguous'
Write-Host 'PASS 4: pinned compatibility and ordered migration gates'

$researchProperties = @($research.PSObject.Properties.Name)
Assert-Condition ($researchProperties -ccontains 'researchedAt') 'Research record is missing researchedAt'
Assert-Condition ($researchProperties -ccontains 'sources') 'Research record is missing sources'
$researchedAt = [DateTimeOffset]::MinValue
Assert-Condition ([DateTimeOffset]::TryParse([string]$research.researchedAt, [ref]$researchedAt)) 'researchedAt is not a valid timestamp'
$researchSources = @($research.sources)
Assert-Condition ($researchSources.Count -ge 1) 'Research record must contain at least one source'
Assert-Condition (@($researchSources.url | Sort-Object -Unique).Count -eq $researchSources.Count) 'Research source URLs must be unique'
foreach ($source in $researchSources) {
    $sourceProperties = @($source.PSObject.Properties.Name)
    foreach ($requiredProperty in @('title', 'url', 'accessedAt', 'informedClaim')) {
        Assert-Condition ($sourceProperties -ccontains $requiredProperty) "Research source is missing $requiredProperty"
        Assert-Condition (-not [string]::IsNullOrWhiteSpace([string]$source.$requiredProperty)) "Research source $requiredProperty is empty"
    }
    $sourceUri = $null
    Assert-Condition ([Uri]::TryCreate([string]$source.url, [UriKind]::Absolute, [ref]$sourceUri)) "Research source URL is invalid: $($source.url)"
    Assert-Condition ($sourceUri.Scheme -ceq 'https') "Research source must use HTTPS: $($source.url)"
    $isBroadcomHost = $sourceUri.Host -ieq 'broadcom.com' -or $sourceUri.Host.EndsWith('.broadcom.com', [StringComparison]::OrdinalIgnoreCase)
    Assert-Condition $isBroadcomHost "Research source is not a Broadcom source: $($source.url)"
    $accessedAt = [DateTimeOffset]::MinValue
    Assert-Condition ([DateTimeOffset]::TryParse([string]$source.accessedAt, [ref]$accessedAt)) "Research source accessedAt is invalid: $($source.url)"
}
Write-Host 'PASS 5: live Broadcom research record'

$moduleManifestPath = Join-Path $Root 'VcfArchitecture/VcfArchitecture.psd1'
$moduleScriptPath = Join-Path $Root 'VcfArchitecture/VcfArchitecture.psm1'
Assert-Condition (Test-Path -LiteralPath $moduleManifestPath -PathType Leaf) 'Missing PowerShell module manifest'
Assert-Condition (Test-Path -LiteralPath $moduleScriptPath -PathType Leaf) 'Missing PowerShell module implementation'
$manifest = Import-PowerShellDataFile -LiteralPath $moduleManifestPath
$requiredModuleNames = @($manifest.RequiredModules | ForEach-Object { if ($_ -is [string]) { $_ } else { $_.ModuleName } })
Assert-Condition ($requiredModuleNames -ccontains 'VMware.Sdk.Vcf.Installer') 'Module manifest must require VMware.Sdk.Vcf.Installer'

$parseTokens = $null
$parseErrors = $null
$moduleAst = [System.Management.Automation.Language.Parser]::ParseFile($moduleScriptPath, [ref]$parseTokens, [ref]$parseErrors)
Assert-Condition (@($parseErrors).Count -eq 0) 'PowerShell module has parser errors'
$sdkCommands = @($moduleAst.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst] -and
    $node.GetCommandName() -clike 'Initialize-VcfInstaller*'
}, $true) | ForEach-Object { $_.GetCommandName() } | Sort-Object -Unique)
Assert-Condition ($sdkCommands -ccontains 'Initialize-VcfInstallerSddcSpec') 'Module does not construct SddcSpec through VMware.Sdk.Vcf.Installer'
Assert-Condition ($sdkCommands -ccontains 'Initialize-VcfInstallerLicenseServerSpec') 'Module does not construct the required VCF 9.1 License Server specification'
Assert-Condition ($sdkCommands.Count -ge 5) 'Module does not materially use the installer SDK model initializers'

$vendoredSdkFiles = @(Get-ChildItem -LiteralPath $Root -Recurse -File | Where-Object Extension -In @('.dll', '.nupkg'))
Assert-Condition ($vendoredSdkFiles.Count -eq 0) 'The solution must not vendor PowerCLI or VMware SDK binaries'

Import-Module $moduleScriptPath -Force -ErrorAction Stop
$generatedArchitecture = New-VcfStretchedArchitecture -Requirements $requirements -CompatibilitySnapshot $snapshot
$generatedMigration = New-VcfMigrationPlan -Inventory $inventory -CompatibilitySnapshot $snapshot
$generatedArchitectureDocument = ($generatedArchitecture | ConvertTo-Json -Depth 100) | ConvertFrom-Json -Depth 100
$generatedMigrationDocument = ($generatedMigration | ConvertTo-Json -Depth 100) | ConvertFrom-Json -Depth 100
Assert-JsonEquivalent $generatedArchitectureDocument $architecture 'Module-generated architecture'
Assert-JsonEquivalent $generatedMigrationDocument $migration 'Module-generated migration plan'
Write-Host 'PASS 6: executable PowerShell module and SDK integration'
Write-Host 'ALL CHECKS PASSED'
