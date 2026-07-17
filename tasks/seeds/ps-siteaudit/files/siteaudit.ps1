# siteaudit.ps1 -- hygiene audit for a published doc-site root.
#
#   pwsh -NoProfile -File siteaudit.ps1 -Root <dir> -Now <iso8601-utc>
#                                       [-MaxBytes <n>] [-MaxAgeDays <n>]
#
# The reference instant always arrives as -Now; this tool never reads the
# wall clock, so audits are reproducible after the fact.
#
# Checks, per file under -Root (recursive):
#   oversize  -- byte length strictly greater than -MaxBytes (default 4096)
#   stale     -- last write strictly more than -MaxAgeDays (default 90)
#                days before -Now
#   badname   -- leaf name contains a space or an uppercase ASCII letter
#
# Output: one '<check> <relative-path>' line per finding, all lines sorted
# ordinal; paths are relative to -Root with '/' separators. Exit 65 when
# there is at least one finding, 0 when the tree is clean.
#
# Errors (stdout stays empty): bad -Now -> exit 64, missing root -> 66.

param(
    [Parameter(Mandatory)][string]$Root,
    [Parameter(Mandatory)][string]$Now,
    [int]$MaxBytes = 4096,
    [int]$MaxAgeDays = 90
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fail {
    param([string]$Message, [int]$Code)
    [Console]::Error.WriteLine("siteaudit: $Message")
    exit $Code
}

$nowUtc = [datetime]::MinValue
$styles = [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
          [System.Globalization.DateTimeStyles]::AdjustToUniversal
if (-not [datetime]::TryParse($Now, [System.Globalization.CultureInfo]::InvariantCulture,
        $styles, [ref]$nowUtc)) {
    Fail "bad -Now value: $Now" 64
}

if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
    Fail "root not found: $Root" 66
}
$prefix = (Get-Item -LiteralPath $Root).FullName

$maxAge = [timespan]::FromDays($MaxAgeDays)
$lines = @()
foreach ($f in @(Get-ChildItem -LiteralPath $prefix -Recurse -File -Force)) {
    $rel = $f.FullName.Substring($prefix.Length).TrimStart('/', '\') -replace '\\', '/'
    if ($f.Length -gt $MaxBytes) {
        $lines += "oversize $rel"
    }
    if (($nowUtc - $f.LastWriteTimeUtc) -gt $maxAge) {
        $lines += "stale $rel"
    }
    if ($f.Name.Contains(' ') -or ($f.Name -cmatch '[A-Z]')) {
        $lines += "badname $rel"
    }
}

[Array]::Sort($lines, [System.StringComparer]::Ordinal)
foreach ($line in $lines) { Write-Output $line }
if ($lines.Count -gt 0) { exit 65 }
exit 0
