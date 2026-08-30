[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $BaseUri,

    [Parameter(Mandatory)]
    [string] $ModulePath,

    [Parameter(Mandatory)]
    [string] $DownloadToken
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

Import-Module $ModulePath -Force

$operation = @(Get-VcfInstallerOperation -Name 'updateDepotSettings' -ErrorAction Stop)
if (
    $operation.Count -ne 1 -or
    $operation[0].CommandInfo.Name -cne 'Invoke-VcfInstallerUpdateDepotSettings'
) {
    throw 'The genuine SDK did not resolve updateDepotSettings uniquely.'
}
$sdkCommands = @(
    'Initialize-VcfInstallerDepotAccount'
    'Initialize-VcfInstallerDepotSettings'
    'Invoke-VcfInstallerUpdateDepotSettings'
)
foreach ($commandName in $sdkCommands) {
    $command = @(
        Get-Command -Name $commandName -Module 'VMware.Sdk.Vcf.Installer' `
            -CommandType Cmdlet -All -ErrorAction SilentlyContinue
    )
    if ($command.Count -ne 1 -or $command[0].Source -cne 'VMware.Sdk.Vcf.Installer') {
        throw "$commandName is not supplied uniquely by the genuine SDK."
    }
}

# `-Server` names an address, not a URL: the SDK builds the URL itself, so
# a base URI handed to it whole became `https://http://127.0.0.1:<port>` and
# could not be parsed. The fixture speaks plain HTTP on a loopback port, and
# both of those have parameters of their own.
$endpoint = [uri] $BaseUri
$connection = Connect-VcfInstallerServer `
    -Server $endpoint.Host `
    -Port $endpoint.Port `
    -Protocol $endpoint.Scheme `
    -User 'admin@local' `
    -Password 'FixtureOnly-Password1!' `
    -ErrorAction Stop

try {
    $result = Set-VcfInstallerDepotToken `
        -Server $connection `
        -DownloadToken $DownloadToken `
        -ErrorAction Stop

    if ($null -eq $result) {
        throw 'Set-VcfInstallerDepotToken returned no result.'
    }

    $result | ConvertTo-Json -Depth 20 -Compress | Write-Output
}
finally {
    # The process exit tears down the loopback-only connection. Calling the SDK
    # disconnect cmdlet could add a token-invalidation operation to the fixture.
    Remove-Module VcfInstallerDepot -Force -ErrorAction SilentlyContinue
}
