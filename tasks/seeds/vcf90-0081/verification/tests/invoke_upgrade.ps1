param(
    [Parameter(Mandatory)]
    [string] $ModulePath,

    [Parameter(Mandatory)]
    [uri] $ServerUri,

    [Parameter(Mandatory)]
    [string] $SessionId,

    [Parameter(Mandatory)]
    [uri] $PakUrl,

    [Parameter(Mandatory)]
    [ValidateRange(0, [int]::MaxValue)]
    [int] $PollIntervalMilliseconds
)

$ErrorActionPreference = 'Stop'
Import-Module $ModulePath -Force

$status = Invoke-VcfLogsUpgrade `
    -ServerUri $ServerUri `
    -SessionId $SessionId `
    -PakUrl $PakUrl `
    -PollIntervalMilliseconds $PollIntervalMilliseconds

$status | ConvertTo-Json -Compress
