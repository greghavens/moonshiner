#!/usr/bin/env bash
# Installs the VCF PowerCLI SDK prerequisite from the PowerShell Gallery.
#
# The VMware.Sdk.Vcf modules are a prerequisite of this repository, not part of
# it. Nothing here is vendored or committed; the version is pinned so the
# acceptance test sees the same build every run.
set -euo pipefail

SDK_MODULE="VMware.Sdk.Vcf.SddcManager"
SDK_VERSION="13.5.0.25380678"

pwsh -NoProfile -NonInteractive -Command "
  \$ErrorActionPreference = 'Stop'
  \$m = Get-Module -ListAvailable -Name '${SDK_MODULE}' |
          Where-Object { \$_.Version.ToString() -eq '${SDK_VERSION}' }
  if (\$m) {
    Write-Host '${SDK_MODULE} ${SDK_VERSION} already installed'
  } else {
    Write-Host 'Installing ${SDK_MODULE} ${SDK_VERSION} from the PowerShell Gallery...'
    Install-Module -Name '${SDK_MODULE}' -RequiredVersion '${SDK_VERSION}' \
                   -Scope CurrentUser -Force -AllowClobber -SkipPublisherCheck
  }
  Import-Module -Name '${SDK_MODULE}' -WarningAction SilentlyContinue
  Write-Host ('Ready: ' + (Get-Module '${SDK_MODULE}').Version)
"
