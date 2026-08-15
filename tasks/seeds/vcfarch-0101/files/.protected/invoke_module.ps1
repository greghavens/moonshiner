param(
    [Parameter(Mandatory)][string]$Workspace,
    [Parameter(Mandatory)][string]$EstatePath,
    [Parameter(Mandatory)][string]$SnapshotPath,
    [Parameter(Mandatory)][string]$OutputDirectory,
    [Parameter(Mandatory)][string]$AuditPath
)

$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$manifest = Join-Path $Workspace 'VcfFleetArchitecture/VcfFleetArchitecture.psd1'
$manifestData = Import-PowerShellDataFile -Path $manifest
$installerRequirements = @(
    $manifestData.RequiredModules | Where-Object {
        $_ -is [System.Collections.IDictionary] -and
        $_['ModuleName'] -ceq 'VMware.Sdk.Vcf.Installer' -and
        [string]$_['RequiredVersion'] -ceq '13.5.0.25380678'
    }
)
if ($installerRequirements.Count -ne 1) {
    throw 'manifest must require VMware.Sdk.Vcf.Installer 13.5.0.25380678'
}
Import-Module $manifest -Force -ErrorAction Stop

$exported = @(Get-Command -Module VcfFleetArchitecture -CommandType Function | Select-Object -ExpandProperty Name)
if ($exported.Count -ne 1 -or $exported[0] -cne 'New-VcfFleetArchitecture') {
    throw "module must export exactly New-VcfFleetArchitecture"
}

$callState = @{ Result = $null }
Trace-Command -Name ParameterBinding -FilePath $AuditPath -Expression {
    $callState.Result = New-VcfFleetArchitecture `
        -EstatePath $EstatePath `
        -CompatibilitySnapshotPath $SnapshotPath `
        -OutputDirectory $OutputDirectory
}
$result = $callState.Result

if ($null -ne $result) {
    throw 'New-VcfFleetArchitecture must not write to the success pipeline'
}
