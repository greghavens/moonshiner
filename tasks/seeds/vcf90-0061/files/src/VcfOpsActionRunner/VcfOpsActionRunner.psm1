#Requires -Version 7.2
Set-StrictMode -Version Latest

# The generated VCF Operations client is installed by the environment. This
# module never vendors it, and never talks to the appliance any other way.
Import-Module VMware.Sdk.Vcf.Ops -ErrorAction Stop -WarningAction SilentlyContinue

$public = @(Get-ChildItem -Path (Join-Path $PSScriptRoot 'Public') -Filter '*.ps1' -ErrorAction SilentlyContinue)
$private = @(Get-ChildItem -Path (Join-Path $PSScriptRoot 'Private') -Filter '*.ps1' -ErrorAction SilentlyContinue)

foreach ($file in ($private + $public)) {
    . $file.FullName
}

Export-ModuleMember -Function $public.BaseName
