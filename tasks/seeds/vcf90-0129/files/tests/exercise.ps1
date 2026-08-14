param(
    [Parameter(Mandatory)]
    [string] $ModulePath,

    [Parameter(Mandatory)]
    [uri] $Server,

    [Parameter(Mandatory)]
    [string] $ResultPath,

    [Parameter(Mandatory)]
    [string] $Name,

    [Parameter(Mandatory)]
    [string] $Token
)

$ErrorActionPreference = 'Stop'
Import-Module $ModulePath -Force

$first = Ensure-VcfNetworkApplication -Server $Server -Token $Token -Name $Name
$second = Ensure-VcfNetworkApplication -Server $Server -Token $Token -Name $Name

[ordered]@{
    DependencyLoaded = [bool](Get-Module -Name VMware.Sdk.Vcf.Ops)
    First = $first
    Second = $second
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ResultPath -Encoding utf8NoBOM
