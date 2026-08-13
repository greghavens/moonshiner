#!/usr/bin/env bash
# Installs the VCF 9.1 PowerCLI prerequisites this repository builds on.
#
# The VMware.Sdk.Vcf modules are published to the PowerShell Gallery and are
# installed here as a prerequisite; nothing in this repository ships a copy of
# them. Installing VMware.Sdk.Vcf.Ops pulls in the shared VMware.OpenAPI
# runtime it depends on.
set -euo pipefail

MODULE="VMware.Sdk.Vcf.Ops"
MODULE_VERSION="13.5.0.25380678"

if ! command -v pwsh >/dev/null 2>&1; then
    echo "error: PowerShell 7 (pwsh) is not on PATH." >&2
    echo "       See https://learn.microsoft.com/powershell/scripting/install/installing-powershell" >&2
    exit 1
fi

echo "pwsh: $(pwsh -NoProfile -NonInteractive -Command '$PSVersionTable.PSVersion.ToString()')"

pwsh -NoProfile -NonInteractive -Command "
    \$ErrorActionPreference = 'Stop'
    \$ProgressPreference = 'SilentlyContinue'

    \$found = Get-Module -ListAvailable -Name '${MODULE}' |
        Where-Object Version -EQ ([version]'${MODULE_VERSION}') |
        Select-Object -First 1
    if (\$found) {
        Write-Host \"${MODULE} \$(\$found.Version) is already installed.\"
        exit 0
    }

    if (-not (Get-PSRepository -Name PSGallery -ErrorAction SilentlyContinue)) {
        Register-PSRepository -Default
    }
    Set-PSRepository -Name PSGallery -InstallationPolicy Trusted

    Write-Host 'Installing ${MODULE} from the PowerShell Gallery...'
    Install-Module -Name '${MODULE}' -RequiredVersion '${MODULE_VERSION}' -Scope CurrentUser -Force -AllowClobber

    \$installed = Get-Module -ListAvailable -Name '${MODULE}' |
        Where-Object Version -EQ ([version]'${MODULE_VERSION}') |
        Select-Object -First 1
    Write-Host \"Installed ${MODULE} \$(\$installed.Version).\"
"

echo "Prerequisites ready."
