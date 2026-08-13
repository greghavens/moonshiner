[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [uri] $ServerUri,

    [Parameter(Mandatory)]
    [string] $ScenarioPath,

    [Parameter(Mandatory)]
    [string] $OutputPath
)

$ErrorActionPreference = 'Stop'

throw 'TODO: load the scenario, run the rollout, and write the report as JSON.'
