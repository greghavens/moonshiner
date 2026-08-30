[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ModuleManifest,
    [Parameter(Mandatory)] [string] $LcmBaseUrl,
    [Parameter(Mandatory)] [string] $AccessToken,
    [Parameter(Mandatory)] [string] $RefreshedAccessToken,
    [Parameter(Mandatory)] [string] $DepotFqdn,
    [Parameter(Mandatory)] [string] $DepotCertificate,
    [Parameter(Mandatory)] [string] $PinnedComponent,
    [Parameter(Mandatory)] [string] $PinnedComponentVersion,
    [Parameter(Mandatory)] [string] $UnpinnedComponent,
    [Parameter(Mandatory)] [string] $CorrelationId,
    [Parameter(Mandatory)] [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$PSStyle.OutputRendering = 'PlainText'

$moduleUnderTest = Import-Module $ModuleManifest -Force -PassThru -ErrorAction Stop
$secureAccessToken = ConvertTo-SecureString -String $AccessToken -AsPlainText -Force
$replacementToken = $RefreshedAccessToken
$refresh = {
    ConvertTo-SecureString -String $replacementToken -AsPlainText -Force
}.GetNewClosure()

# The pinned component carries an explicit version; the unpinned one deliberately
# omits the optional 'version' key entirely so its absence must survive to the wire.
$componentVersions = @(
    @{ component = $PinnedComponent; version = $PinnedComponentVersion },
    @{ component = $UnpinnedComponent }
)

# -TargetVersion is intentionally not passed: DepotComponentsSpec.version is optional
# and its absence must also survive to the wire.
$output = @(
    Invoke-VcfSddcLcmDepotSync `
        -AccessToken $secureAccessToken `
        -RefreshAccessToken $refresh `
        -LcmBaseUrl $LcmBaseUrl `
        -DepotFqdn $DepotFqdn `
        -DepotCertificate $DepotCertificate `
        -ComponentVersion $componentVersions `
        -CorrelationId $CorrelationId `
        -ErrorAction Stop
)
if ($output.Count -ne 1) {
    throw "Invoke-VcfSddcLcmDepotSync returned $($output.Count) objects."
}
$result = $output[0]

$resolved = @(
    foreach ($item in @($result.ResolvedComponents)) {
        [ordered] @{
            component = [string] $item.component
            version   = [string] $item.version
            binaryUrl = [string] $item.binaryUrl
        }
    }
)

$record = [ordered] @{
    taskId               = [string] $result.TaskId
    taskStatus           = [string] $result.TaskStatus
    resolvedComponents   = $resolved
    refreshCount         = [int] $result.AccessTokenRefreshCount
}
$json = $record | ConvertTo-Json -Depth 8 -Compress
[IO.File]::WriteAllText(
    $OutputPath,
    $json,
    [Text.UTF8Encoding]::new($false)
)
