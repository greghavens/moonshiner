[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [int] $Port,

    [Parameter(Mandatory)]
    [string] $OutputFile
)

$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'

$root = Split-Path -Parent $PSScriptRoot
$manifest = Join-Path $root 'src/VcfSddcManager.CredentialInventory.psd1'
$sdkVersion = [version] '13.5.0.25380678'

$installed = Get-Module -ListAvailable -Name VMware.Sdk.Vcf.SddcManager |
    Where-Object Version -EQ $sdkVersion |
    Select-Object -First 1
if ($null -eq $installed) {
    throw 'Required environment prerequisite VMware.Sdk.Vcf.SddcManager 13.5.0.25380678 is not installed.'
}

Import-Module $manifest -Force

foreach ($sdkCommandName in @('Connect-VcfSddcManagerServer', 'Invoke-VcfGetCredentials')) {
    $sdkCommand = Get-Command $sdkCommandName -CommandType Cmdlet -ErrorAction Stop
    if ($sdkCommand.Source -cne 'VMware.Sdk.Vcf.SddcManager') {
        throw "$sdkCommandName did not resolve to the genuine SDDC Manager SDK module."
    }
}

$candidate = Get-Command Get-VcfSddcManagerCredentialInventory `
    -CommandType Function -ErrorAction Stop
$candidateExports = @(Get-Command -Module $candidate.ModuleName -CommandType Function)
if ($candidateExports.Count -ne 1 -or
    $candidateExports[0].Name -cne 'Get-VcfSddcManagerCredentialInventory') {
    throw 'The candidate module must export exactly Get-VcfSddcManagerCredentialInventory.'
}

$expectedTypes = [ordered]@{
    Server = [object]
    PageSize = [int]
    ResourceName = [string]
    ResourceType = [string]
    DomainName = [string]
    AccountType = [string]
}
foreach ($entry in $expectedTypes.GetEnumerator()) {
    $parameter = $candidate.Parameters[$entry.Key]
    if ($null -eq $parameter -or $parameter.ParameterType -ne $entry.Value) {
        throw "Public parameter $($entry.Key) is missing or has the wrong type."
    }
    $mandatory = @(
        $parameter.Attributes |
            Where-Object { $_ -is [System.Management.Automation.ParameterAttribute] }
    )[0].Mandatory
    if ($mandatory -ne ($entry.Key -ceq 'Server')) {
        throw "Public parameter $($entry.Key) has the wrong Mandatory contract."
    }
}

$pageSizeRange = @(
    $candidate.Parameters.PageSize.Attributes |
        Where-Object { $_ -is [System.Management.Automation.ValidateRangeAttribute] }
)
if ($pageSizeRange.Count -ne 1 -or
    [int] $pageSizeRange[0].MinRange -ne 1 -or
    [int] $pageSizeRange[0].MaxRange -ne 1000) {
    throw 'PageSize must retain ValidateRange(1, 1000).'
}

$resourceTypeSet = @(
    $candidate.Parameters.ResourceType.Attributes |
        Where-Object { $_ -is [System.Management.Automation.ValidateSetAttribute] }
)
if ($resourceTypeSet.Count -ne 1 -or
    (@($resourceTypeSet[0].ValidValues) -join ',') -cne
        'ESXI,VCENTER,PSC,NSXT_MANAGER,NSXT_EDGE,NSX_ALB,BACKUP') {
    throw 'ResourceType must retain the resource types the pinned 9.0.0.0 contract lists.'
}

$accountTypeSet = @(
    $candidate.Parameters.AccountType.Attributes |
        Where-Object { $_ -is [System.Management.Automation.ValidateSetAttribute] }
)
if ($accountTypeSet.Count -ne 1 -or
    (@($accountTypeSet[0].ValidValues) -join ',') -cne 'USER,SYSTEM,SERVICE') {
    throw 'AccountType must retain the account types the pinned 9.0.0.0 contract lists.'
}

$defaultText = @{}
foreach ($parameterAst in $candidate.ScriptBlock.Ast.Body.ParamBlock.Parameters) {
    $name = $parameterAst.Name.VariablePath.UserPath
    $defaultText[$name] = if ($null -eq $parameterAst.DefaultValue) {
        ''
    }
    else {
        $parameterAst.DefaultValue.Extent.Text
    }
}
if ($defaultText.PageSize -cne '100') {
    throw 'PageSize must keep its default of 100.'
}

$serverConnection = Connect-VcfSddcManagerServer `
    -Server '127.0.0.1' `
    -Port $Port `
    -Protocol 'http' `
    -User 'moonshiner-verifier' `
    -Password (ConvertTo-SecureString 'local-loopback-password' -AsPlainText -Force) `
    -NotDefault

$scenarios = [ordered]@{}
$typeNames = [System.Collections.Generic.List[string]]::new()

function Invoke-Scenario {
    param(
        [string] $Name,
        [hashtable] $Arguments
    )

    $items = @(Get-VcfSddcManagerCredentialInventory -Server $serverConnection @Arguments)

    # Build the projection in a typed list so an empty or single-element result still
    # serialises as a JSON array instead of null or a bare string.
    $projection = [System.Collections.Generic.List[string]]::new()
    foreach ($item in $items) {
        $typeNames.Add($item.GetType().FullName)
        $projection.Add(('{0}|{1}|{2}|{3}' -f
                $item.Resource.ResourceName, $item.CredentialType, $item.Username, $item.Id))
    }
    $scenarios[$Name] = $projection.ToArray()
}

Invoke-Scenario -Name 'pageSize4' -Arguments @{ PageSize = 4 }
Invoke-Scenario -Name 'pageSize3' -Arguments @{ PageSize = 3 }
Invoke-Scenario -Name 'pageSize9' -Arguments @{ PageSize = 9 }
Invoke-Scenario -Name 'esxiMgmtDomain' -Arguments @{
    PageSize = 2
    ResourceType = 'ESXI'
    DomainName = 'mgmt-domain'
}
Invoke-Scenario -Name 'serviceAccounts' -Arguments @{
    PageSize = 5
    AccountType = 'SERVICE'
}
Invoke-Scenario -Name 'singleResource' -Arguments @{
    PageSize = 2
    ResourceName = 'esx-01.vrack.vsphere.local'
}
Invoke-Scenario -Name 'emptyCollection' -Arguments @{
    PageSize = 4
    DomainName = 'no-such-domain'
}

$uniqueTypeNames = [System.Collections.Generic.List[string]]::new()
foreach ($typeName in ($typeNames | Sort-Object -Unique)) {
    $uniqueTypeNames.Add([string] $typeName)
}

[ordered]@{
    scenarios = $scenarios
    elementTypeNames = $uniqueTypeNames.ToArray()
} | ConvertTo-Json -Depth 6 |
    Set-Content -LiteralPath $OutputFile -Encoding utf8NoBOM
