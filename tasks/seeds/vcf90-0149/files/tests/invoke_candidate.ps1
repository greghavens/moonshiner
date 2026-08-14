param(
    [Parameter(Mandatory)]
    [string] $ModuleManifest,

    [Parameter(Mandatory)]
    [uri] $Server,

    [Parameter(Mandatory)]
    [string] $CaseFile
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$case = Get-Content -LiteralPath $CaseFile -Raw | ConvertFrom-Json
Import-Module -Name $ModuleManifest -Force

if (-not (Get-Module -Name VMware.Sdk.Vcf.SddcManager)) {
    throw 'The manifest did not load its VMware.Sdk.Vcf.SddcManager prerequisite.'
}

$command = Get-Command -Name Invoke-VcfAutomationUpdateProject -CommandType Function
$requiredParameters = @('Server', 'ApiToken', 'Id', 'Name')
$optionalParameters = @(
    'Description',
    'Administrators',
    'Members',
    'Viewers',
    'Supervisors',
    'ZoneAssignmentConfigurations',
    'Constraints',
    'OperationTimeout',
    'MachineNamingTemplate',
    'SharedResources',
    'PlacementPolicy',
    'CustomProperties',
    'ApiVersion',
    'ValidatePrincipals'
)
foreach ($parameterName in $requiredParameters) {
    $metadata = $command.Parameters[$parameterName]
    if ($null -eq $metadata) {
        throw "Parameter $parameterName must exist and be mandatory."
    }
    $attribute = $metadata.Attributes |
        Where-Object { $_ -is [System.Management.Automation.ParameterAttribute] } |
        Select-Object -First 1
    if ($null -eq $attribute -or -not $attribute.Mandatory) {
        throw "Parameter $parameterName must exist and be mandatory."
    }
}
foreach ($parameterName in $optionalParameters) {
    $metadata = $command.Parameters[$parameterName]
    if ($null -eq $metadata) {
        throw "Parameter $parameterName must exist and be optional."
    }
    $attribute = $metadata.Attributes |
        Where-Object { $_ -is [System.Management.Automation.ParameterAttribute] } |
        Select-Object -First 1
    if ($null -ne $attribute -and $attribute.Mandatory) {
        throw "Parameter $parameterName must exist and be optional."
    }
}

$common = @{
    Server = $Server
    ApiToken = $case.apiToken
    Id = $case.retryProjectId
    Name = $case.retryName
}

$first = Invoke-VcfAutomationUpdateProject @common
$second = Invoke-VcfAutomationUpdateProject @common
$third = Invoke-VcfAutomationUpdateProject `
    -Server $Server `
    -ApiToken $case.apiToken `
    -Id $case.explicitProjectId `
    -Name $case.explicitName `
    -Description '' `
    -OperationTimeout 0 `
    -SharedResources:$false `
    -ApiVersion $case.apiVersion `
    -ValidatePrincipals:$false
$full = Invoke-VcfAutomationUpdateProject `
    -Server $Server `
    -ApiToken $case.apiToken `
    -Id $case.fullProjectId `
    -Name $case.full.name `
    -Description $case.full.description `
    -Administrators $case.full.administrators `
    -Members $case.full.members `
    -Viewers $case.full.viewers `
    -Supervisors $case.full.supervisors `
    -ZoneAssignmentConfigurations $case.full.zoneAssignmentConfigurations `
    -Constraints $case.full.constraints `
    -OperationTimeout $case.full.operationTimeout `
    -MachineNamingTemplate $case.full.machineNamingTemplate `
    -SharedResources:$true `
    -PlacementPolicy $case.full.placementPolicy `
    -CustomProperties $case.full.customProperties `
    -ApiVersion $case.apiVersion `
    -ValidatePrincipals:$true

if ($first.id -ne $case.retryProjectId -or $first.name -ne $case.retryName) {
    throw 'The first parsed response did not contain the expected project values.'
}
if ($second.id -ne $case.retryProjectId -or $second.name -ne $case.retryName) {
    throw 'The retry parsed response did not contain the expected project values.'
}
if ($third.id -ne $case.explicitProjectId -or $third.name -ne $case.explicitName) {
    throw 'The explicit-values parsed response did not contain the expected project values.'
}
if ($full.id -ne $case.fullProjectId -or $full.name -ne $case.full.name) {
    throw 'The full-contract parsed response did not contain the expected project values.'
}
