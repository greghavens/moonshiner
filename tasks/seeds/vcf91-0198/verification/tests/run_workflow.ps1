[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [uri] $ServerUri,

    [Parameter()]
    [ValidateSet('Unbound', 'True', 'False')]
    [string] $SkipValidationsMode = 'Unbound',

    [Parameter()]
    [ValidateRange(1, 10000)]
    [int] $MaxPollCount = 6
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$root = Split-Path -Parent $PSScriptRoot
$manifest = Join-Path $root 'VcfInstaller.Resilient/VcfInstaller.Resilient.psd1'
Import-Module $manifest -Force -ErrorAction Stop

$securePassword = ConvertTo-SecureString 'Example-Installer1!Pass' -AsPlainText -Force
$credential = [pscredential]::new('admin@local', $securePassword)
$specification = Get-Content (Join-Path $root 'fixtures/sddc-spec.json') -Raw |
    ConvertFrom-Json -AsHashtable

$deploymentParameters = @{
    ServerUri = $ServerUri
    Credential = $credential
    SddcSpec = $specification
    PollIntervalSeconds = 0
    MaxPollCount = $MaxPollCount
}
if ($SkipValidationsMode -eq 'True') {
    $deploymentParameters.SkipValidations = $true
}
elseif ($SkipValidationsMode -eq 'False') {
    $deploymentParameters.SkipValidations = $false
}

$result = Invoke-VcfInstallerResilientDeployment @deploymentParameters

$result | ConvertTo-Json -Depth 20 -Compress
