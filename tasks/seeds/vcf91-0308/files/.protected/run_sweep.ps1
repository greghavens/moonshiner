#Requires -Version 7.2
<#
    Protected driver. Imports the VcfAutomation module manifest, runs one sweep against
    the contract-pinned loopback mock, and writes either the result or a sanitized error
    as JSON/text for the verifier.

    No live VMware endpoint is contacted.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ManifestPath,
    [Parameter(Mandatory)] [string] $ApiEndpoint,
    [Parameter(Mandatory)] [string] $Tenant,
    [Parameter(Mandatory)] [string] $ApiToken,
    [Parameter(Mandatory)] [string] $AccessToken,
    [Parameter(Mandatory)] [string] $ActionId,
    [Parameter()] [string] $Reason,
    [Parameter()] [string] $InputsJson,
    [Parameter(Mandatory)] [int]    $PageSize,
    [Parameter(Mandatory)] [string] $ResultPath,
    [Parameter(Mandatory)] [string] $ErrorPath
)

$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'

try {
    Import-Module -Name $ManifestPath -Force -ErrorAction Stop

    $arguments = @{
        ApiEndpoint = $ApiEndpoint
        Tenant      = $Tenant
        ApiToken    = $ApiToken
        AccessToken = $AccessToken
        ActionId    = $ActionId
        PageSize    = $PageSize
        TimeoutSec  = 5
    }
    if ($PSBoundParameters.ContainsKey('Reason')) {
        $arguments['Reason'] = $Reason
    }
    if ($PSBoundParameters.ContainsKey('InputsJson')) {
        $arguments['Inputs'] = $InputsJson | ConvertFrom-Json -AsHashtable -ErrorAction Stop
    }

    $result = Invoke-VcfaDeploymentActionSweep @arguments

    $json = @($result) | ConvertTo-Json -Depth 8 -AsArray
    [System.IO.File]::WriteAllText($ResultPath, $json, [System.Text.UTF8Encoding]::new($false))
    exit 0
}
catch {
    $detail = @(
        "message: $($_.Exception.Message)"
        "type: $($_.Exception.GetType().FullName)"
        "position: $($_.InvocationInfo.PositionMessage)"
    ) -join [Environment]::NewLine
    [System.IO.File]::WriteAllText($ErrorPath, $detail, [System.Text.UTF8Encoding]::new($false))
    exit 1
}
