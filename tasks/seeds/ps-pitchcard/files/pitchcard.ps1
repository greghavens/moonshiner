# pitchcard.ps1 -- print arrival cards for today's pitch assignments.
# The gate crew hands these to campers at check-in, one card per pitch.
# Usage: pwsh -NoProfile -File pitchcard.ps1 -Path <pitches.csv> [-Season peak]
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

param(
    [Parameter(Mandatory)][string]$Path,
    [ValidateSet('standard', 'peak')][string]$Season = 'standard'
)

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    [Console]::Error.WriteLine("pitchcard: assignment list not found: $Path")
    exit 66
}

$rows = @(Import-Csv -LiteralPath $Path)
Write-Output ('arrivals: {0} pitches ({1} season)' -f $rows.Count, $Season)
foreach ($p in $rows) {
    Write-Output ('-- pitch {0} zone {1} --' -f $p.id, $p.zone)
    Write-Output ('  party of {0}, {1} nights' -f $p.party, $p.nights)
    $fee = [int]$p.nights * 12
    if ($Season = 'peak') {
        $fee += [int]$p.nights * 3
        Write-Output ('  fee: {0} (peak rate)' -f $fee)
    } else {
        Write-Output ('  fee: {0}' -f $fee)
    }
    Write-Output "  pitch $p.id ready"
}
exit 0
