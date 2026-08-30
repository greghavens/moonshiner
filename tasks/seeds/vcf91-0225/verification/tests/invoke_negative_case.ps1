[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ModuleManifest,
    [Parameter(Mandatory)] [string] $ServiceUri,
    [Parameter(Mandatory)] [string] $AccessToken,
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

Import-Module $ModuleManifest -Force -ErrorAction Stop

$plan = Get-Content -LiteralPath $PlanPath -Raw | ConvertFrom-Json
$serviceUriValue = [uri] $ServiceUri
$secureAccessToken = ConvertTo-SecureString $AccessToken -AsPlainText -Force
$threw = $false
$exceptionType = ''
$exceptionMessage = ''

try {
    $arguments = @{
        ServiceUri          = $serviceUriValue
        AccessToken         = $secureAccessToken
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
    }
    $json = $output | ConvertTo-Json -Depth 6 -Compress
    [IO.File]::WriteAllText($OutputPath, $json, [Text.UTF8Encoding]::new($false))
} finally { }
