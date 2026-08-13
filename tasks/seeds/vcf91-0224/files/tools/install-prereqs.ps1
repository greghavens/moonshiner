<#
.SYNOPSIS
    Installs the VMware.Sdk.Vcf PowerCLI prerequisite from the PowerShell Gallery.

.DESCRIPTION
    VcfSddcLcm declares VMware.Sdk.Vcf.SddcManager in its manifest's
    RequiredModules. The SDK is a published PSGallery package and is installed
    here as an environment prerequisite; it is deliberately never vendored into
    this repository.

    Installing VMware.Sdk.Vcf.SddcManager also pulls its dependency chain
    (VMware.OpenAPI, VMware.Vim, VMware.VimAutomation.*), roughly 270 MB.
#>
[CmdletBinding()]
param(
    [string] $RequiredVersion = '13.5.0.25380678',
    [ValidateSet('CurrentUser', 'AllUsers')]
    [string] $Scope = 'CurrentUser'
)

$ErrorActionPreference = 'Stop'

if (Get-Module -ListAvailable -Name VMware.Sdk.Vcf.SddcManager) {
    Write-Host "VMware.Sdk.Vcf.SddcManager already available."
}
else {
    Write-Host "Installing VMware.Sdk.Vcf.SddcManager $RequiredVersion from PSGallery..."
    Install-Module -Name VMware.Sdk.Vcf.SddcManager `
        -RequiredVersion $RequiredVersion `
        -Repository PSGallery -Scope $Scope -Force -AllowClobber
}

# PowerCLI prompts about its telemetry programme on first import, which is noise
# in a non-interactive verifier run.
try {
    Import-Module VMware.VimAutomation.Common -ErrorAction SilentlyContinue
    Set-PowerCLIConfiguration -Scope User -ParticipateInCEIP $false -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
} catch { }

$m = Get-Module -ListAvailable -Name VMware.Sdk.Vcf.SddcManager |
     Sort-Object Version -Descending | Select-Object -First 1
if (-not $m) { throw "VMware.Sdk.Vcf.SddcManager is still not available after install." }
Write-Host ("Ready: {0} {1}" -f $m.Name, $m.Version)
