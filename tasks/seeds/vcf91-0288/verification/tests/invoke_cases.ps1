#Requires -Version 7.4
<#
    Protected harness. Loads the candidate module directly from its .psm1 (so
    the manifest's RequiredModules are not resolved) and runs every verifier
    case against its own loopback mock, writing a JSON result document.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $ModulePath,
    [Parameter(Mandatory)][string] $CasesPath,
    [Parameter(Mandatory)][string] $OutPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function New-CaseCredential {
    param($Spec)
    if ($null -eq $Spec) { return $null }
    $secure = ConvertTo-SecureString -String ([string]$Spec.password) -AsPlainText -Force
    return [pscredential]::new([string]$Spec.username, $secure)
}

$document = [ordered]@{
    importError = $null
    commandFound = $false
    exportedFunctions = @()
    cases = @()
}

try {
    $module = Import-Module -Name $ModulePath -Force -PassThru -ErrorAction Stop
    $document.exportedFunctions = @($module.ExportedFunctions.Keys | Sort-Object)
}
catch {
    $document.importError = $_.Exception.Message
    $document | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $OutPath -Encoding utf8
    exit 0
}

$command = Get-Command -Name 'Add-VcfNetworksVcenterDataSource' -ErrorAction SilentlyContinue
if ($null -eq $command) {
    $document | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $OutPath -Encoding utf8
    exit 0
}
$document.commandFound = $true

$cases = @(Get-Content -LiteralPath $CasesPath -Raw | ConvertFrom-Json)

foreach ($case in $cases) {
    $params = @{}
    foreach ($property in $case.params.PSObject.Properties) {
        $value = $property.Value
        if ($property.Name -in @('Credential', 'VcenterCredential')) {
            $value = New-CaseCredential $value
        }
        if ($null -ne $value) {
            $params[$property.Name] = $value
        }
    }

    $record = [ordered]@{
        name          = [string]$case.name
        threw         = $false
        exceptionType = $null
        message       = $null
        outputCount   = 0
        outputs       = @()
        outputTypes   = @()
    }

    try {
        $outputs = @(& $command @params)
        $record.outputCount = $outputs.Count
        $record.outputs = $outputs
        $record.outputTypes = @($outputs | ForEach-Object {
            if ($null -eq $_) { 'null' } else { $_.GetType().FullName }
        })
    }
    catch {
        $record.threw = $true
        $exception = $_.Exception
        if ($exception -is [System.Management.Automation.MethodInvocationException] -and $null -ne $exception.InnerException) {
            $exception = $exception.InnerException
        }
        $record.exceptionType = $exception.GetType().FullName
        $record.message = [string]$exception.Message
    }

    $document.cases += $record
}

$document | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $OutPath -Encoding utf8
exit 0
