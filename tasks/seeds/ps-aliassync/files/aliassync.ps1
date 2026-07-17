# aliassync.ps1 -- reconcile the host-alias directory against desired state.
#
#   pwsh -NoProfile -File aliassync.ps1 -Desired <aliases.json> -Dir <dir>
#
# desired json: { "aliases": [ { "name": "cache", "target": "10.0.4.7" } ] }
#
# The directory holds one <name>.alias file per managed alias; its content
# is the target plus a trailing newline. Anything not ending in .alias is
# not ours and is never touched.
#
# A run prints its plan -- create/update/remove lines, ordinal by name
# within each section, in that section order -- and then applies it.
# Nothing to do means no output. Exit 0 on success.
#
# Errors (stdout stays empty, nothing is touched):
#   66  desired file or alias dir missing
#   65  duplicate or invalid alias name in desired state

param(
    [Parameter(Mandatory)][string]$Desired,
    [Parameter(Mandatory)][string]$Dir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fail {
    param([string]$Message, [int]$Code)
    [Console]::Error.WriteLine("aliassync: $Message")
    exit $Code
}

if (-not (Test-Path -LiteralPath $Desired -PathType Leaf)) {
    Fail "desired file not found: $Desired" 66
}
if (-not (Test-Path -LiteralPath $Dir -PathType Container)) {
    Fail "alias dir not found: $Dir" 66
}

$doc = Get-Content -LiteralPath $Desired -Raw | ConvertFrom-Json
$wanted = [System.Collections.Generic.Dictionary[string, string]]::new([System.StringComparer]::Ordinal)
foreach ($entry in @($doc.aliases)) {
    $name = [string]$entry.name
    if ($name -cnotmatch '^[a-z0-9][a-z0-9-]*$') {
        Fail "bad alias name: $name" 65
    }
    if ($wanted.ContainsKey($name)) {
        Fail "duplicate alias: $name" 65
    }
    $wanted[$name] = [string]$entry.target
}

$current = [System.Collections.Generic.Dictionary[string, string]]::new([System.StringComparer]::Ordinal)
foreach ($f in @(Get-ChildItem -LiteralPath $Dir -File)) {
    if ($f.Name -clike '*.alias') {
        $name = $f.Name.Substring(0, $f.Name.Length - 6)
        $current[$name] = (Get-Content -LiteralPath $f.FullName -Raw).TrimEnd("`n")
    }
}

function Sort-Ordinal {
    param([string[]]$Values)
    $copy = @($Values)
    [Array]::Sort($copy, [System.StringComparer]::Ordinal)
    return $copy
}

$creates = @()
$updates = @()
foreach ($name in @(Sort-Ordinal @($wanted.Keys))) {
    if (-not $current.ContainsKey($name)) {
        $creates += $name
    } elseif ($current[$name] -cne $wanted[$name]) {
        $updates += $name
    }
}
$removes = @()
foreach ($name in @(Sort-Ordinal @($current.Keys))) {
    if (-not $wanted.ContainsKey($name)) {
        $removes += $name
    }
}

foreach ($name in $creates) { Write-Output "create $name" }
foreach ($name in $updates) { Write-Output "update $name" }
foreach ($name in $removes) { Write-Output "remove $name" }

foreach ($name in $creates) {
    $path = Join-Path $Dir "$name.alias"
    [System.IO.File]::WriteAllText($path, $wanted[$name] + "`n")
}
foreach ($name in $updates) {
    $path = Join-Path $Dir "$name.alias"
    [System.IO.File]::WriteAllText($path, $wanted[$name] + "`n")
}
foreach ($name in $removes) {
    Remove-Item -LiteralPath (Join-Path $Dir "$name.alias")
}

exit 0
