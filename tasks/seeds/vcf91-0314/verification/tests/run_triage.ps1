#Requires -Version 7.2
<#
    Test driver. Imports the VcfAutomation.Triage module, runs the triage
    orchestrator against the loopback mock and writes the resulting report to
    disk as JSON so the Python verifier can assert on it.

    Protected: do not edit. The verifier invokes this exact script.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $BaseUri,
    [Parameter(Mandatory)][string] $AccessToken,
    [Parameter(Mandatory)][string] $DeploymentName,
    [Parameter(Mandatory)][string] $OutFile
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

try {
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $manifest = Join-Path $repoRoot 'src/VcfAutomation.Triage/VcfAutomation.Triage.psd1'

    Import-Module $manifest -Force -ErrorAction Stop

    $report = Invoke-VcfAutomationDeploymentTriage `
        -BaseUri $BaseUri `
        -AccessToken $AccessToken `
        -DeploymentName $DeploymentName

    if ($null -eq $report) {
        throw 'Invoke-VcfAutomationDeploymentTriage returned nothing.'
    }

    $report | ConvertTo-Json -Depth 8 | Set-Content -Path $OutFile -Encoding utf8
    exit 0
}
catch {
    Write-Host '--- triage driver failed ---'
    Write-Host $_.Exception.Message
    if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace }
    exit 1
}
