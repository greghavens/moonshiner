<#
.SYNOPSIS
    Takes one inventory snapshot against the loopback mock and records the result.

.DESCRIPTION
    Runs in its own process so that a fault in the module under test -- including
    one that never returns -- cannot take the verifier down with it.

    It owns the session bootstrap: Connect-VcfOpsServer is called here, not in the
    module, so the module is handed a connection exactly as a caller would hand it
    one. The number of mock log lines the bootstrap produced is written to
    -BoundaryPath, and the verifier asserts only on requests after that mark, so
    the bootstrap never contaminates the wire assertions.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [int]    $Port,
    [Parameter(Mandatory)] [string] $ModulePath,
    [Parameter(Mandatory)] [string] $LogPath,
    [Parameter(Mandatory)] [string] $BoundaryPath,
    [Parameter(Mandatory)] [string] $ResultPath,
    [Parameter(Mandatory)] [int]    $PageSize,
    # pwsh -File hands every argument over as a string, so list-valued filters
    # arrive comma separated and are split back apart here.
    [string] $Name,
    [string] $AdapterKind,
    [string] $ResourceKind,
    [string] $ResourceHealth,
    [long]   $CreatedAfter,
    [string] $PropertyName,
    [string] $PropertyValue,
    # An empty string is hard to preserve through Start-Process -ArgumentList.
    # This harness-only switch binds both string filters explicitly to ''.
    [switch] $BindEmptyStringFilters
)

$ErrorActionPreference = 'Stop'

Import-Module VMware.Sdk.Vcf.Ops -ErrorAction Stop -WarningAction SilentlyContinue 3>$null
Import-Module $ModulePath -Force -ErrorAction Stop

$server = Connect-VcfOpsServer -Server 127.0.0.1 -Port $Port -Protocol http `
    -User 'svc-inventory' -Password (ConvertTo-SecureString 'inventory-secret' -AsPlainText -Force) `
    -NotDefault -IgnoreInvalidCertificate -WarningAction SilentlyContinue 3>$null

# Everything logged up to here belongs to the bootstrap, not to the module.
$boundary = 0
if (Test-Path -LiteralPath $LogPath) {
    $boundary = @(Get-Content -LiteralPath $LogPath | Where-Object { $_.Trim() }).Count
}
Set-Content -LiteralPath $BoundaryPath -Value $boundary -NoNewline

$callArgs = @{
    Server   = $server
    PageSize = $PageSize
}
if ($Name)           { $callArgs.Name           = @($Name           -split ',' | Where-Object { $_ }) }
if ($AdapterKind)    { $callArgs.AdapterKind    = @($AdapterKind    -split ',' | Where-Object { $_ }) }
if ($ResourceKind)   { $callArgs.ResourceKind   = @($ResourceKind   -split ',' | Where-Object { $_ }) }
if ($ResourceHealth) { $callArgs.ResourceHealth = @($ResourceHealth -split ',' | Where-Object { $_ }) }
if ($PSBoundParameters.ContainsKey('CreatedAfter')) { $callArgs.CreatedAfter = $CreatedAfter }
if ($BindEmptyStringFilters) {
    $callArgs.PropertyName = ''
    $callArgs.PropertyValue = ''
}
else {
    if ($PSBoundParameters.ContainsKey('PropertyName'))  { $callArgs.PropertyName  = $PropertyName }
    if ($PSBoundParameters.ContainsKey('PropertyValue')) { $callArgs.PropertyValue = $PropertyValue }
}

$snapshot = Get-VcfOpsResourceInventory @callArgs

$snapshot | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ResultPath
exit 0
