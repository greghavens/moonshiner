<#
.SYNOPSIS
    Drives one triage run against the loopback mock and records the result.

.DESCRIPTION
    Runs in its own process so that a fault in the module under test cannot take
    the verifier down with it.

    It owns the session bootstrap: Connect-VcfOpsServer is called here, not in the
    module, so the caller supplies the connection and the module owns only the
    token lifetime of its own run. The number of mock log lines produced by that
    bootstrap is written to -BoundaryPath; the verifier asserts only on requests
    after that mark, so the bootstrap never contaminates the wire assertions.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [int]    $Port,
    [Parameter(Mandatory)] [string] $ModulePath,
    [Parameter(Mandatory)] [string] $LogPath,
    [Parameter(Mandatory)] [string] $BoundaryPath,
    [Parameter(Mandatory)] [string] $ResultPath,
    [Parameter(Mandatory)] [string] $Action,
    [int]    $PageSize,
    [switch] $ActiveOnly,
    [string] $AuthSource,
    [int]    $SuspendMinutes,
    [string] $OwnerAccountId,
    [string] $ResourceKind,
    # Comma-separated: pwsh -File hands every argument over as a single string.
    [string] $AlertCriticality
)

$ErrorActionPreference = 'Stop'

Import-Module VMware.Sdk.Vcf.Ops -ErrorAction Stop -WarningAction SilentlyContinue 3>$null
Import-Module $ModulePath -Force -ErrorAction Stop

$server = Connect-VcfOpsServer -Server 127.0.0.1 -Port $Port -Protocol http `
    -User 'bootstrap' -Password 'bootstrap-secret' -IgnoreInvalidCertificate `
    -WarningAction SilentlyContinue 3>$null

# Everything logged up to here belongs to the bootstrap, not to the module.
$boundary = 0
if (Test-Path -LiteralPath $LogPath) {
    $boundary = @(Get-Content -LiteralPath $LogPath).Count
}
Set-Content -LiteralPath $BoundaryPath -Value $boundary -NoNewline

$secure = ConvertTo-SecureString 'triage-secret' -AsPlainText -Force
$credential = [pscredential]::new('svc-triage', $secure)

$callArgs = @{
    Server     = $server
    Credential = $credential
    Action     = $Action
}
if ($PSBoundParameters.ContainsKey('PageSize')) {
    $callArgs.PageSize = $PageSize
}
if ($ActiveOnly) {
    $callArgs.ActiveOnly = $true
}
if ($PSBoundParameters.ContainsKey('AuthSource') -and $AuthSource) {
    $callArgs.AuthSource = $AuthSource
}
if ($PSBoundParameters.ContainsKey('SuspendMinutes') -and $SuspendMinutes -gt 0) {
    $callArgs.SuspendMinutes = $SuspendMinutes
}
if ($PSBoundParameters.ContainsKey('OwnerAccountId') -and $OwnerAccountId) {
    $callArgs.OwnerAccountId = $OwnerAccountId
}
if ($PSBoundParameters.ContainsKey('ResourceKind') -and $ResourceKind) {
    $callArgs.ResourceKind = $ResourceKind
}
if ($PSBoundParameters.ContainsKey('AlertCriticality') -and $AlertCriticality) {
    $callArgs.AlertCriticality = @($AlertCriticality -split ',' | Where-Object { $_ })
}

$summary = Invoke-VcfOpsAlertTriage @callArgs

$summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ResultPath
exit 0
