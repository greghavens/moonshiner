param(
    [Parameter(Mandatory)]
    [string] $ModulePath,

    [Parameter(Mandatory)]
    [uri] $Server,

    [Parameter(Mandatory)]
    [string] $AccessToken,

    [Parameter(Mandatory)]
    [string] $ResultPath,

    [string] $ApiVersion,

    [string] $Filter,

    [switch] $ExpectFailure,

    [int] $PageSize = 3
)

$ErrorActionPreference = 'Stop'
Import-Module -Name $ModulePath -Force -ErrorAction Stop

$prerequisite = Get-Module -Name VMware.Sdk.Vcf.Installer
if ($null -eq $prerequisite) {
    throw 'The VMware.Sdk.Vcf.Installer prerequisite was not loaded by the module manifest.'
}

$inventoryParameters = @{
    Server = $Server
    AccessToken = $AccessToken
    PageSize = $PageSize
}
if ($PSBoundParameters.ContainsKey('ApiVersion')) {
    $inventoryParameters.ApiVersion = $ApiVersion
}
if ($PSBoundParameters.ContainsKey('Filter')) {
    $inventoryParameters.Filter = $Filter
}

try {
    $projects = @(Get-VcfAutomationProjectInventory @inventoryParameters)
    $inventoryError = $null
}
catch {
    $inventoryError = $_
}

if ($ExpectFailure) {
    if ($null -eq $inventoryError) {
        throw 'The inventory command succeeded when failure was expected.'
    }
    $result = [ordered]@{
        prerequisiteVersion = $prerequisite.Version.ToString()
        succeeded = $false
        error = $inventoryError.Exception.Message
    }
}
else {
    if ($null -ne $inventoryError) {
        throw $inventoryError
    }
    $result = [ordered]@{
        prerequisiteVersion = $prerequisite.Version.ToString()
        succeeded = $true
        projects = $projects
    }
}
$result | ConvertTo-Json -Depth 20 -Compress | Set-Content -LiteralPath $ResultPath -NoNewline
