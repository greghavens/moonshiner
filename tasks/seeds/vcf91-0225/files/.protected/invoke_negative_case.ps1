[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ModuleManifest,
    [Parameter(Mandatory)] [string] $MockHost,
    [Parameter(Mandatory)] [int]    $MockPort,
    [Parameter(Mandatory)] [string] $User,
    [Parameter(Mandatory)] [string] $Password,
    [Parameter(Mandatory)] [string] $DepotFqdn,
    [Parameter(Mandatory)] [string] $DepotCertificate,
    [Parameter(Mandatory)] [string] $PlanPath,
    [Parameter(Mandatory)] [ValidateSet('resolution', 'timeout')] [string] $Mode,
    [Parameter(Mandatory)] [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$PSStyle.OutputRendering = 'PlainText'

Import-Module 'VMware.Sdk.Vcf.Installer' `
    -RequiredVersion '13.5.0.25380678' `
    -Force `
    -ErrorAction Stop
Import-Module $ModuleManifest -Force -ErrorAction Stop

$plan = Get-Content -LiteralPath $PlanPath -Raw | ConvertFrom-Json
$server = Connect-VcfInstallerServer `
    -Server $MockHost `
    -Port $MockPort `
    -Protocol 'http' `
    -User $User `
    -Password $Password `
    -NotDefault `
    -IgnoreInvalidCertificate `
    -ErrorAction Stop

$tokenBefore = [string] $server.SessionSecret
$serviceUriBefore = [string] $server.ServiceUri
$threw = $false
$exceptionType = ''
$exceptionMessage = ''

try {
    $arguments = @{
        Server              = $server
        Component           = $plan
        DepotFqdn           = $DepotFqdn
        DepotCertificate    = $DepotCertificate
        PollIntervalSeconds = if ($Mode -ceq 'timeout') { 1 } else { 0 }
        TimeoutSeconds      = if ($Mode -ceq 'timeout') { 1 } else { 10 }
    }
    try {
        $null = Invoke-VcfSddcLcmComponentUpgrade @arguments -ErrorAction Stop
    } catch {
        $threw = $true
        $exceptionType = $_.Exception.GetType().FullName
        $exceptionMessage = $_.Exception.Message
    }

    $output = [ordered] @{
        threw               = [bool] $threw
        exceptionType       = [string] $exceptionType
        exceptionMessage    = [string] $exceptionMessage
        sessionStillOpen    = [bool] $server.IsConnected
        tokenUnchanged      = ([string] $server.SessionSecret -ceq $tokenBefore)
        serviceUriUnchanged = ([string] $server.ServiceUri -ceq $serviceUriBefore)
    }
    $json = $output | ConvertTo-Json -Depth 6 -Compress
    [IO.File]::WriteAllText($OutputPath, $json, [Text.UTF8Encoding]::new($false))
} finally {
    if ($null -ne $server -and $server.IsConnected) {
        Disconnect-VcfInstallerServer -Server $server `
            -ErrorAction SilentlyContinue | Out-Null
    }
}
