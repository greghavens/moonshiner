# registrar.ps1 -- stamp one welcome card. Called once per member by
# enrolldesk.ps1; prints the card manifest line the front office files.
param(
    [Parameter(Mandatory)][string]$Name,
    [Parameter(Mandatory)][string]$Team,
    [Parameter(Mandatory)][string]$Code
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Write-Output ('card|{0}|{1}|len={2}|code={3}' -f $Name, $Team, $Code.Length, $Code)
exit 0
