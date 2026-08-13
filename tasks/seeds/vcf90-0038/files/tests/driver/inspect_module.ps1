# PROTECTED FILE -- do not modify.
#
# Reports the candidate module's manifest and exported surface as JSON so the
# Python test can assert on it.

[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ManifestPath,
    [Parameter(Mandatory)] [string] $OutFile
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$report = [ordered]@{
    ok               = $false
    error            = $null
    manifestValid    = $false
    requiredModules  = @()
    exportedFunctions = @()
    loadedSdkModules = @()
    rootModule       = $null
    powerShellVersion = $null
}

try {
    $data = Import-PowerShellDataFile -LiteralPath $ManifestPath -ErrorAction Stop

    if ($data.ContainsKey('RootModule')) { $report.rootModule = $data['RootModule'] }
    if ($data.ContainsKey('PowerShellVersion')) { $report.powerShellVersion = [string]$data['PowerShellVersion'] }

    if ($data.ContainsKey('RequiredModules')) {
        $report.requiredModules = @(
            foreach ($entry in @($data['RequiredModules'])) {
                if ($entry -is [hashtable]) { [string]$entry['ModuleName'] }
                else { [string]$entry }
            }
        )
    }

    # Test-ModuleManifest also proves the RequiredModules entries resolve.
    $manifest = Test-ModuleManifest -Path $ManifestPath -ErrorAction Stop
    $report.manifestValid = $true

    Import-Module $ManifestPath -Force -ErrorAction Stop -WarningAction SilentlyContinue
    $module = Get-Module -Name $manifest.Name | Select-Object -First 1
    $report.exportedFunctions = @($module.ExportedFunctions.Keys | Sort-Object)
    $report.loadedSdkModules = @(
        Get-Module | Where-Object { $_.Name -like 'VMware.Sdk.Vcf.*' } |
            ForEach-Object { $_.Name } | Sort-Object
    )
    $report.ok = $true
}
catch {
    $report.error = ($_ | Out-String)
}

$report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $OutFile -Encoding utf8
