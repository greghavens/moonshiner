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

$connection = Connect-VcfInstallerServer `
    -Server $BaseUri `
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
