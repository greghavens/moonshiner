param(
    [Parameter(Mandatory)]
    [string] $ModulePath,

    [Parameter(Mandatory)]
    [string] $JobPath,

    [Parameter(Mandatory)]
    [uri] $Server,

    [Parameter(Mandatory)]
    [string] $ResultPath
)

$ErrorActionPreference = 'Stop'
Import-Module -Name $ModulePath -Force -ErrorAction Stop

$prerequisite = Get-Module -Name VMware.Sdk.Vcf.Installer
if ($null -eq $prerequisite) {
    throw 'The VMware.Sdk.Vcf.Installer prerequisite was not loaded by the module manifest.'
}

$job = Get-Content -LiteralPath $JobPath -Raw | ConvertFrom-Json
$updated = @(
    Sync-VcfAutomationProject `
        -Server $Server `
        -RefreshToken $job.refreshToken `
        -Project $job.projects
)

$result = [ordered]@{
    prerequisiteVersion = $prerequisite.Version.ToString()
    updated = $updated
}
$result | ConvertTo-Json -Depth 30 -Compress | Set-Content -LiteralPath $ResultPath -NoNewline
