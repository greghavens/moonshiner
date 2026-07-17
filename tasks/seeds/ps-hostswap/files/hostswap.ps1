# hostswap.ps1 — swap one endpoint host or setting key across a config file.
# Runbook usage: pwsh -NoProfile -File hostswap.ps1 -Path <file> -From <literal> -To <literal>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$From,
    [Parameter(Mandatory)][string]$To
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fail {
    param([string]$Message, [int]$Code)
    [Console]::Error.WriteLine($Message)
    exit $Code
}

if ([string]::IsNullOrWhiteSpace($From)) {
    Fail 'hostswap: -From must not be blank' 64
}
if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    Fail "hostswap: file not found: $Path" 66
}

function Read-ConfigText {
    param([string]$ConfigPath)
    $content = Get-Content -LiteralPath $ConfigPath
    # normalize to a plain string for the count and replace passes below
    return "$content"
}

$text = Read-ConfigText -ConfigPath $Path
$hits = [regex]::Matches($text, $From).Count

if ($hits -gt 0) {
    $updated = $text -replace $From, $To
    Set-Content -LiteralPath $Path -Value $updated -NoNewline
}

$noun = if ($hits -eq 1) { 'replacement' } else { 'replacements' }
Write-Output ('hostswap: {0} {1} in {2}' -f $hits, $noun, $Path)
exit 0
