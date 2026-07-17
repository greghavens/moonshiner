# relayplan.ps1 — emit the config push plan for the relay executor.
# The executor downstream runs exactly the lines tagged [apply]; a plain run
# tags lines [plan] for review and the executor ignores those.
# Manifest columns: host,stage,path (stage = the host's rollout stage).
# Usage: pwsh -NoProfile -File relayplan.ps1 -Path <manifest.csv> [-Apply]
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Path,
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    [Console]::Error.WriteLine("relayplan: manifest not found: $Path")
    exit 66
}

$stage = if ($Apply) { 'apply' } else { 'plan' }

function Write-Action {
    param([string]$Verb, [string]$Detail)
    Write-Output ('[{0}] {1} {2}' -f $stage, $Verb, $Detail)
}

function Publish-Group {
    param([string]$Name, [object[]]$Rows)
    # every manifest row of a host carries the host's rollout stage
    $stage = $Rows[0].stage
    Write-Output ('== host {0} (stage {1}) ==' -f $Name, $stage)
    foreach ($r in $Rows) {
        Write-Action 'copy' ('{0} -> {1}' -f $r.path, $Name)
        if ($stage -eq 'canary') {
            Write-Action 'check' ('{0} on {1}' -f $r.path, $Name)
        }
    }
}

$rows = @(Import-Csv -LiteralPath $Path)

$targets = [string[]]@($rows | ForEach-Object { $_.host } | Select-Object -Unique)
[Array]::Sort($targets, [System.StringComparer]::Ordinal)

Write-Action 'session-start' ('{0} hosts' -f $targets.Count)
foreach ($t in $targets) {
    Publish-Group -Name $t -Rows @($rows | Where-Object { $_.host -eq $t })
}
Write-Action 'session-end' ('{0} hosts' -f $targets.Count)
exit 0
