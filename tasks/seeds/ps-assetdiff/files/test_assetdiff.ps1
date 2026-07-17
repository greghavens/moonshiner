# Acceptance harness for assetdiff.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_assetdiff.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$tool = Join-Path $PSScriptRoot 'assetdiff.ps1'
if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
    Write-Output 'FAIL assetdiff.ps1 not found in the workspace root'
    exit 1
}

# The hand-rolled reconciliation is the point of this tool; the team banned
# Compare-Object for it, and that ban is part of the contract.
$src = [System.IO.File]::ReadAllText($tool).ToLowerInvariant()

$T = Join-Path $PSScriptRoot '_t'
$script:checks = 0
$script:fails = 0

function Assert-Eq {
    param([string]$Label, [string]$Expected, [string]$Actual)
    $script:checks++
    if ($Expected -ceq $Actual) { return }
    $script:fails++
    Write-Output "FAIL $Label"
    Write-Output '--- expected ---'
    Write-Output $Expected
    Write-Output '--- actual ---'
    Write-Output $Actual
    Write-Output '----------------'
}

function Assert-True {
    param([string]$Label, [bool]$Condition)
    $script:checks++
    if ($Condition) { return }
    $script:fails++
    Write-Output "FAIL $Label"
}

Assert-True 'source does not use Compare-Object' (-not $src.Contains('compare-object'))

$script:RC = 0
$script:OUT = ''
$script:ERR = ''
function Invoke-Tool {
    param([string[]]$CaseArgs = @())
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'assetdiff.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function Write-Fixture {
    param([string]$Name, [string]$Content)
    $p = Join-Path $T $Name
    [System.IO.File]::WriteAllText($p, $Content)
    return $p
}

$mainExpected = @'
{
  "added": [
    {
      "Tag": "AB-9",
      "Model": "cart-3",
      "Site": "fra",
      "Owner": "field",
      "Rack": "r2"
    }
  ],
  "removed": [
    {
      "Tag": "AB-7",
      "Model": "edge-9",
      "Site": "zrh",
      "Owner": "net"
    }
  ],
  "changed": [
    {
      "key": "AB-10",
      "fields": [
        {
          "name": "Rack",
          "before": null,
          "after": "r7"
        }
      ]
    },
    {
      "key": "AB-2",
      "fields": [
        {
          "name": "Owner",
          "before": "Ops",
          "after": "ops"
        },
        {
          "name": "Rack",
          "before": null,
          "after": ""
        }
      ]
    },
    {
      "key": "ab-1",
      "fields": [
        {
          "name": "Rack",
          "before": null,
          "after": ""
        }
      ]
    }
  ]
}

'@

$cleanExpected = @'
{
  "added": [],
  "removed": [],
  "changed": []
}

'@

$serialExpected = @'
{
  "added": [],
  "removed": [
    {
      "Serial": "ZX-9",
      "State": "live"
    }
  ],
  "changed": [
    {
      "key": "ZX-2",
      "fields": [
        {
          "name": "State",
          "before": "live",
          "after": "idle"
        }
      ]
    }
  ]
}

'@

$firstSweepExpected = @'
{
  "added": [
    {
      "Tag": "B-10",
      "Model": "edge"
    },
    {
      "Tag": "B-2",
      "Model": "cart"
    }
  ],
  "removed": [],
  "changed": []
}

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- the main reconciliation: added, removed, case-sensitive value
    # --- changes, and a column that only exists in the newer snapshot ---
    $before = Write-Fixture 'before.csv' "Tag,Model,Site,Owner`nAB-10,edge-7,ams,ops`nAB-2,edge-7,fra,Ops`nab-1,cart-3,ams,`nAB-7,edge-9,zrh,net`n"
    $after = Write-Fixture 'after.csv' "Tag,Model,Site,Owner,Rack`nAB-10,edge-7,ams,ops,r7`nAB-2,edge-7,fra,ops,`nAB-9,cart-3,fra,field,r2`nab-1,cart-3,ams,,`n"
    Invoke-Tool @('-Before', $before, '-After', $after)
    Assert-True 'main: exit 0' ($RC -eq 0)
    Assert-Eq 'main: stderr empty' '' $ERR
    Assert-Eq 'main: report' $mainExpected $OUT

    # --- identical snapshots produce an empty (but fully shaped) report ---
    Invoke-Tool @('-Before', $before, '-After', $before)
    Assert-True 'clean: exit 0' ($RC -eq 0)
    Assert-Eq 'clean: report' $cleanExpected $OUT

    # --- -Key picks the identity column ---
    $kb = Write-Fixture 'kb.csv' "Serial,State`nZX-9,live`nZX-2,live`n"
    $ka = Write-Fixture 'ka.csv' "Serial,State`nZX-2,idle`n"
    Invoke-Tool @('-Before', $kb, '-After', $ka, '-Key', 'Serial')
    Assert-True 'serial: exit 0' ($RC -eq 0)
    Assert-Eq 'serial: report' $serialExpected $OUT

    # --- header-only snapshot: everything in the other file is added,
    # --- ordinal-sorted by key ('B-10' sorts before 'B-2') ---
    $empty = Write-Fixture 'empty.csv' "Tag,Model`n"
    $two = Write-Fixture 'two.csv' "Tag,Model`nB-2,cart`nB-10,edge`n"
    Invoke-Tool @('-Before', $empty, '-After', $two)
    Assert-True 'firstsweep: exit 0' ($RC -eq 0)
    Assert-Eq 'firstsweep: report' $firstSweepExpected $OUT

    # --- duplicate key in a snapshot is a data error ---
    $dup = Write-Fixture 'dup.csv' "Tag,Model`nA-1,x`nA-1,y`n"
    Invoke-Tool @('-Before', $dup, '-After', $two)
    Assert-True 'dup: exit 65' ($RC -eq 65)
    Assert-Eq 'dup: stdout empty' '' $OUT
    Assert-Eq 'dup: message' "assetdiff: duplicate key 'A-1' in $dup`n" $ERR

    # --- key column must exist in a snapshot that has rows ---
    Invoke-Tool @('-Before', $kb, '-After', $two)
    Assert-True 'badkey: exit 65' ($RC -eq 65)
    Assert-Eq 'badkey: message' "assetdiff: key column 'Tag' not found in $kb`n" $ERR

    # --- missing file ---
    $gone = Join-Path $T 'gone.csv'
    Invoke-Tool @('-Before', $gone, '-After', $two)
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "assetdiff: file not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
