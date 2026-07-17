# patchreport.ps1 — render the kiosk patch-status report from a collector snapshot.
# The collector runs on the management host and drops hosts.json next to its
# logs; this script only formats what the collector saw.
# Usage: pwsh -NoProfile -File patchreport.ps1 -Path <hosts.json>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    [Console]::Error.WriteLine("patchreport: snapshot not found: $Path")
    exit 66
}

$records = @(Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json)

# hosts the collector could not attribute carry owners = null; those are out
# of scope for this report
$tracked = @($records | Where-Object { $_.owners -ne $null })

$sites = [string[]]@($tracked | ForEach-Object { $_.site } | Select-Object -Unique)
[Array]::Sort($sites, [System.StringComparer]::Ordinal)

function Format-OwnerText {
    param($Owners)
    $named = @($Owners | Where-Object { $_ })
    if ($named.Count -eq 0) { return '(unassigned)' }
    return ($named -join ', ')
}

Write-Output 'patch status report'
$listed = 0
foreach ($site in $sites) {
    Write-Output "== site $site =="
    $siteHosts = @($tracked | Where-Object { $_.site -eq $site })
    $names = [string[]]@($siteHosts | ForEach-Object { $_.name })
    [Array]::Sort($names, [System.StringComparer]::Ordinal)
    $byName = @{}
    foreach ($h in $siteHosts) { $byName[$h.name] = $h }
    foreach ($n in $names) {
        $entry = $byName[$n]
        $mark = if ($entry.status -eq 'overdue') { '!' } else { ' ' }
        Write-Output ('{0} {1}  {2}' -f $mark, $n.PadRight(14), (Format-OwnerText $entry.owners))
        $listed++
    }
}

$overdueSites = $tracked | Where-Object { $_.status -eq 'overdue' } | Group-Object -Property site
Write-Output ('hosts listed: {0}' -f $listed)
Write-Output ('overdue sites: {0}' -f $overdueSites.Count)
exit 0
