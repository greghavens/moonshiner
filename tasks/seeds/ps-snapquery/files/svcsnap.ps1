# svcsnap.ps1 — query a service-state snapshot taken by the fleet collector.
# The collector runs on the monitoring host and drops a JSON array of
# {name, state, restarts} records; this tool answers questions about one
# of those snapshot files without anyone having to open it in an editor.
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Snapshot,

    [Parameter(Mandatory, ParameterSetName = 'ByName')]
    [string]$Name,

    [Parameter(ParameterSetName = 'ByName')]
    [ValidateRange(1, 10000)]
    [int]$Limit,

    [Parameter(Mandatory, ParameterSetName = 'ByState')]
    [ValidateSet('failed', 'running', 'stopped')]
    [string]$State,

    [switch]$CountOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Snapshot -PathType Leaf)) {
    [Console]::Error.WriteLine("svcsnap: snapshot not found: $Snapshot")
    exit 2
}

$rows = @(Get-Content -LiteralPath $Snapshot -Raw | ConvertFrom-Json)

if ($PSBoundParameters.ContainsKey('Name')) {
    $rows = @($rows | Where-Object { $_.name -ceq $Name })
}
elseif ($PSBoundParameters.ContainsKey('State')) {
    $rows = @($rows | Where-Object { $_.state -ceq $State })
}

# Deterministic ordering: ordinal by name, never the culture collation.
$keys = [string[]]@($rows | ForEach-Object { [string]$_.name })
[Array]::Sort($keys, [System.StringComparer]::Ordinal)
$byName = @{}
foreach ($row in $rows) {
    $byName[[string]$row.name] = $row
}
$ordered = @(foreach ($k in $keys) { $byName[$k] })

if ($PSBoundParameters.ContainsKey('Limit')) {
    $ordered = @($ordered | Select-Object -First $Limit)
}

if ($CountOnly) {
    Write-Output "count=$($ordered.Count)"
    exit 0
}

if ($ordered.Count -gt 0) {
    $lines = foreach ($row in $ordered) {
        '{0} {1} restarts={2}' -f $row.name, $row.state, $row.restarts
    }
    Write-Output ($lines -join "`n")
}
exit 0
