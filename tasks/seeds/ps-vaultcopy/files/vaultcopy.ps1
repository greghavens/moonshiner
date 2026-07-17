# vaultcopy.ps1 — stage the nightly vault set.
# Reads a manifest of files to protect and copies each one into the staging
# folder the vault agent picks up, preserving the manifest's relative layout.
# Manifest entries are relative to the ops checkout root (the folder this
# script lives in); lines starting with # and blank lines are ignored.
# Usage: pwsh -NoProfile -File vaultcopy.ps1 -Manifest <file> -Dest <dir>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Manifest,
    [Parameter(Mandatory)][string]$Dest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Manifest -PathType Leaf)) {
    [Console]::Error.WriteLine("vaultcopy: manifest not found: $Manifest")
    exit 66
}

# entries resolve against the ops checkout root
$root = (Get-Location).Path

$copied = 0
$missing = 0
foreach ($line in [System.IO.File]::ReadAllLines($Manifest)) {
    $entry = $line.Trim()
    if ($entry.Length -eq 0 -or $entry.StartsWith('#')) { continue }

    $src = Join-Path $root $entry
    if (Test-Path $src -PathType Leaf) {
        $dst = Join-Path $Dest $entry
        $dstDir = Split-Path -Parent $dst
        if (-not (Test-Path -LiteralPath $dstDir -PathType Container)) {
            New-Item -ItemType Directory -Force -Path $dstDir > $null
        }
        Copy-Item $src -Destination $dst
        Write-Output "copied $entry"
        $copied++
    } else {
        Write-Output "missing $entry"
        $missing++
    }
}

Write-Output "copied: $copied"
Write-Output "missing: $missing"
if ($missing -gt 0) { exit 65 }
exit 0
