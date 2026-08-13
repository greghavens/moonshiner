<#
    Installs the VMware PowerCLI SDK this repository depends on.

    The SDK is an environment prerequisite. It is deliberately not vendored into
    the repository; install it here and let the module consume it from the
    module path.
#>
[CmdletBinding()]
param(
    [string]$ModuleName = 'VMware.Sdk.Vcf.SddcManager',
    [string]$Scope = 'CurrentUser'
)

$ErrorActionPreference = 'Stop'

if (Get-Module -ListAvailable -Name $ModuleName) {
    Write-Host "$ModuleName is already installed."
    Get-Module -ListAvailable -Name $ModuleName | Select-Object Name, Version | Format-Table -AutoSize
    return
}

$repository = Get-PSRepository -Name 'PSGallery' -ErrorAction SilentlyContinue
if ($repository -and $repository.InstallationPolicy -ne 'Trusted') {
    Set-PSRepository -Name 'PSGallery' -InstallationPolicy Trusted
}

Install-Module -Name $ModuleName -Scope $Scope -Force -AllowClobber -SkipPublisherCheck
Get-Module -ListAvailable -Name $ModuleName | Select-Object Name, Version | Format-Table -AutoSize
