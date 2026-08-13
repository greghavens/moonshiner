[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ModuleManifest,
    [Parameter(Mandatory)] [string] $SddcManagerHost,
    [Parameter(Mandatory)] [int]    $SddcManagerPort,
    [Parameter(Mandatory)] [string] $LcmBaseUrl,
    [Parameter(Mandatory)] [string] $Username,
    [Parameter(Mandatory)] [string] $Password,
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

Import-Module 'VMware.Sdk.Vcf.SddcManager' `
    -RequiredVersion '13.5.0.25380678' `
    -Force `
    -ErrorAction Stop
$moduleUnderTest = Import-Module $ModuleManifest -Force -PassThru -ErrorAction Stop

# Resolve the required session commands from inside the module-under-test's own
# session state. This proves an implementation did not shadow the generated SDK
# cmdlets with lookalike functions or aliases that merely imitate their HTTP traffic.
$sessionCommands = @(
    & $moduleUnderTest {
        foreach ($name in @(
            'Initialize-VcfTokenCreationSpec',
            'Invoke-VcfCreateToken',
            'Invoke-VcfRefreshAccessToken'
        )) {
            Get-Command -Name $name -ErrorAction Stop
        }
    }
)
if ($sessionCommands.Count -ne 3) {
    throw 'The three generated SDK session commands did not resolve uniquely.'
}
foreach ($command in $sessionCommands) {
    if (
        $command.CommandType -ne [Management.Automation.CommandTypes]::Cmdlet -or
        $command.Source -cne 'VMware.Sdk.Vcf.SddcManager'
    ) {
        throw "$($command.Name) must resolve to the genuine VMware SDK cmdlet."
    }
}

$securePassword = ConvertTo-SecureString -String $Password -AsPlainText -Force
$credential = [pscredential]::new($Username, $securePassword)

# Caller-owned genuine SDK connection. The module under test must not connect or
# disconnect it; it only borrows it as the transport for the session operations.
$server = Connect-VcfSddcManagerServer `
    -Server $SddcManagerHost `
    -Protocol 'http' `
    -Port $SddcManagerPort `
    -User $Username `
    -Password $securePassword `
    -IgnoreInvalidCertificate `
    -NotDefault `
    -ErrorAction Stop

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
        -Server $server `
        -Credential $credential `
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

# The caller-owned connection is deliberately left connected and is never disconnected
# here: the verifier asserts the module under test did not tear it down, and
# Disconnect-VcfSddcManagerServer would issue invalidateRefreshToken, which this
# focused contract does not name.
$record = [ordered] @{
    taskId               = [string] $result.TaskId
    taskStatus           = [string] $result.TaskStatus
    resolvedComponents   = $resolved
    refreshCount         = [int] $result.AccessTokenRefreshCount
    serverStillConnected = [bool] $server.IsConnected
    serverUser           = [string] $server.User
}
$json = $record | ConvertTo-Json -Depth 8 -Compress
[IO.File]::WriteAllText(
    $OutputPath,
    $json,
    [Text.UTF8Encoding]::new($false)
)
