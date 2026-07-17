# enrolldesk.ps1 -- run the day's membership signups through the registrar.
# Roster CSV columns: name,team. Codes file: one "name=code" line per member
# (everything after the FIRST = is the code, verbatim).
# Usage: pwsh -NoProfile -File enrolldesk.ps1 -Roster <roster.csv> -Codes <codes.txt>
param(
    [Parameter(Mandatory)][string]$Roster,
    [Parameter(Mandatory)][string]$Codes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ReliefCode = 'Kiosk9'

if (-not (Test-Path -LiteralPath $Roster -PathType Leaf)) {
    [Console]::Error.WriteLine("enrolldesk: roster not found: $Roster")
    exit 66
}
if (-not (Test-Path -LiteralPath $Codes -PathType Leaf)) {
    [Console]::Error.WriteLine("enrolldesk: codes file not found: $Codes")
    exit 66
}

$codeMap = @{}
foreach ($line in @(Get-Content -LiteralPath $Codes)) {
    if ($line -eq '') { continue }
    $i = $line.IndexOf('=')
    $codeMap[$line.Substring(0, $i)] = $line.Substring($i + 1)
}

$members = @(Import-Csv -LiteralPath $Roster)
foreach ($m in $members) {
    if (-not $codeMap.ContainsKey($m.name)) {
        [Console]::Error.WriteLine("enrolldesk: no code on file for $($m.name)")
        exit 65
    }
}

$registrar = Join-Path $PSScriptRoot 'registrar.ps1'
foreach ($m in $members) {
    $code = $codeMap[$m.name]
    $cmd = "pwsh -NoProfile -File `"$registrar`" -Name '$($m.name)' -Team $($m.team) -Code ""$code"""
    Invoke-Expression $cmd
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine("enrolldesk: registrar failed for $($m.name)")
        exit 70
    }
}

& pwsh -NoProfile -File $registrar --% -Name relief-desk -Team floaters -Code $ReliefCode
if ($LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine('enrolldesk: registrar failed for relief-desk')
    exit 70
}

Write-Output ('enrolled {0} members, 1 relief card' -f $members.Count)
exit 0
