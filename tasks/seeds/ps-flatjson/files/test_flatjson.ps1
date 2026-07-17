# Acceptance harness for flatjson.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_flatjson.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'flatjson.ps1') -PathType Leaf)) {
    Write-Output 'FAIL flatjson.ps1 not found in the workspace root'
    exit 1
}

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

$script:RC = 0
$script:OUT = ''
$script:ERR = ''
function Invoke-Tool {
    param([string[]]$CaseArgs = @())
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'flatjson.ps1') @CaseArgs 1>$outFile 2>$errFile
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

$mainJson = @'
[
  {
    "name": "kiosk-11",
    "online": true,
    "agent": { "build": 412, "channel": "stable" },
    "disk": { "data": { "freeGb": 52, "quotaGb": 64 } },
    "note": null
  },
  {
    "name": "kiosk-3",
    "online": false,
    "agent": { "build": 398, "channel": "beta" },
    "disk": { "data": { "freeGb": 9, "quotaGb": 64 } },
    "note": "replace ssd"
  }
]
'@

$mainCsv = @'
"agent.build","agent.channel","disk.data.freeGb","disk.data.quotaGb","name","note","online"
"412","stable","52","64","kiosk-11","","true"
"398","beta","9","64","kiosk-3","replace ssd","false"

'@

$mainExpanded = @'
[
  {
    "agent": {
      "build": 412,
      "channel": "stable"
    },
    "disk": {
      "data": {
        "freeGb": 52,
        "quotaGb": 64
      }
    },
    "name": "kiosk-11",
    "note": null,
    "online": true
  },
  {
    "agent": {
      "build": 398,
      "channel": "beta"
    },
    "disk": {
      "data": {
        "freeGb": 9,
        "quotaGb": 64
      }
    },
    "name": "kiosk-3",
    "note": "replace ssd",
    "online": false
  }
]

'@

$raggedCsv = @'
"hw.ram","name","note"
"8","a-1",""
"","b-2","loaner"

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- flatten: dotted columns, ordinal-sorted union; int/bool/null cells ---
    $main = Write-Fixture 'records.json' $mainJson
    Invoke-Tool @('-Mode', 'flatten', '-Path', $main)
    Assert-True 'flatten: exit 0' ($RC -eq 0)
    Assert-Eq 'flatten: stderr empty' '' $ERR
    Assert-Eq 'flatten: csv' $mainCsv $OUT

    # --- expand: types come back (int/bool/null), nesting rebuilt ---
    $csvPath = Write-Fixture 'main.csv' $mainCsv
    Invoke-Tool @('-Mode', 'expand', '-Path', $csvPath)
    Assert-True 'expand: exit 0' ($RC -eq 0)
    Assert-Eq 'expand: stderr empty' '' $ERR
    Assert-Eq 'expand: json' $mainExpanded $OUT

    # --- the full round trip is stable: flatten(expand(csv)) == csv ---
    $rtJson = Write-Fixture 'roundtrip.json' $OUT
    Invoke-Tool @('-Mode', 'flatten', '-Path', $rtJson)
    Assert-True 'roundtrip: exit 0' ($RC -eq 0)
    Assert-Eq 'roundtrip: csv identical' $mainCsv $OUT

    # --- ragged records: union of columns, absent paths become empty cells ---
    $ragged = Write-Fixture 'ragged.json' '[{"name": "a-1", "hw": {"ram": 8}}, {"name": "b-2", "note": "loaner"}]'
    Invoke-Tool @('-Mode', 'flatten', '-Path', $ragged)
    Assert-True 'ragged: exit 0' ($RC -eq 0)
    Assert-Eq 'ragged: csv' $raggedCsv $OUT

    # --- a single-record file is still a table (classic unrolling trap) ---
    $single = Write-Fixture 'single.json' '[{"solo": {"just": "one"}}]'
    Invoke-Tool @('-Mode', 'flatten', '-Path', $single)
    Assert-True 'single: exit 0' ($RC -eq 0)
    Assert-Eq 'single: csv' "`"solo.just`"`n`"one`"`n" $OUT

    # --- empty JSON array flattens to nothing; header-only CSV expands to [] ---
    $none = Write-Fixture 'none.json' '[]'
    Invoke-Tool @('-Mode', 'flatten', '-Path', $none)
    Assert-True 'emptyjson: exit 0' ($RC -eq 0)
    Assert-Eq 'emptyjson: stdout empty' '' $OUT
    $headerOnly = Write-Fixture 'headeronly.csv' "`"name`",`"ok`"`n"
    Invoke-Tool @('-Mode', 'expand', '-Path', $headerOnly)
    Assert-True 'emptycsv: exit 0' ($RC -eq 0)
    Assert-Eq 'emptycsv: json' "[]`n" $OUT

    # --- guard rails, all exit 65 with empty stdout ---
    $deep = Write-Fixture 'deep.json' '[{"a": {"b": {"c": {"d": 1}}}}]'
    Invoke-Tool @('-Mode', 'flatten', '-Path', $deep)
    Assert-True 'deep: exit 65' ($RC -eq 65)
    Assert-Eq 'deep: stdout empty' '' $OUT
    Assert-Eq 'deep: message' "flatjson: nesting too deep at 'a.b.c'`n" $ERR

    $float = Write-Fixture 'float.json' '[{"pi": 3.5}]'
    Invoke-Tool @('-Mode', 'flatten', '-Path', $float)
    Assert-True 'float: exit 65' ($RC -eq 65)
    Assert-Eq 'float: message' "flatjson: unsupported value at 'pi'`n" $ERR

    $arr = Write-Fixture 'arr.json' '[{"tags": ["a"]}]'
    Invoke-Tool @('-Mode', 'flatten', '-Path', $arr)
    Assert-True 'arrval: exit 65' ($RC -eq 65)
    Assert-Eq 'arrval: message' "flatjson: unsupported value at 'tags'`n" $ERR

    $guard = Write-Fixture 'guard.json' '[{"flag": "true"}]'
    Invoke-Tool @('-Mode', 'flatten', '-Path', $guard)
    Assert-True 'guardbool: exit 65' ($RC -eq 65)
    Assert-Eq 'guardbool: message' "flatjson: string value at 'flag' would not round-trip`n" $ERR

    $guard2 = Write-Fixture 'guard2.json' '[{"code": "0091"}]'
    Invoke-Tool @('-Mode', 'flatten', '-Path', $guard2)
    Assert-True 'guarddigits: exit 65' ($RC -eq 65)
    Assert-Eq 'guarddigits: message' "flatjson: string value at 'code' would not round-trip`n" $ERR

    $conflict = Write-Fixture 'conflict.csv' "`"a`",`"a.b`"`n`"x`",`"y`"`n"
    Invoke-Tool @('-Mode', 'expand', '-Path', $conflict)
    Assert-True 'conflict: exit 65' ($RC -eq 65)
    Assert-Eq 'conflict: message' "flatjson: column 'a.b' conflicts with column 'a'`n" $ERR

    $deepCsv = Write-Fixture 'deepcsv.csv' "`"w.x.y.z`"`n`"1`"`n"
    Invoke-Tool @('-Mode', 'expand', '-Path', $deepCsv)
    Assert-True 'deepcsv: exit 65' ($RC -eq 65)
    Assert-Eq 'deepcsv: message' "flatjson: nesting too deep at 'w.x.y.z'`n" $ERR

    # --- missing file ---
    $gone = Join-Path $T 'gone.json'
    Invoke-Tool @('-Mode', 'flatten', '-Path', $gone)
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: message' "flatjson: file not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
