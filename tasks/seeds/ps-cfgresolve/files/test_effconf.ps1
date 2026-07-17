# Acceptance harness for effconf.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_effconf.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'effconf.ps1') -PathType Leaf)) {
    Write-Output 'FAIL effconf.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'effconf.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function Write-Config {
    param([string]$Name, [string]$Json)
    $p = Join-Path $T $Name
    [System.IO.File]::WriteAllText($p, $Json)
    return $p
}

$defaults = "BatchSize=50`nCompress=false`nEndpoint=http://127.0.0.1:9009/ingest`nFlushSeconds=10`nRegion=local`n"

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- no config, no args: the built-in defaults, ordinal-sorted ---
    Invoke-Tool @()
    Assert-True 'defaults: exit 0' ($RC -eq 0)
    Assert-Eq 'defaults: resolved lines' $defaults $OUT
    Assert-Eq 'defaults: stderr empty' '' $ERR

    # --- config file overrides only the keys it names ---
    $cfg = Write-Config 'partial.json' '{"BatchSize": 200, "Region": "eu-1"}'
    Invoke-Tool @('-Config', $cfg)
    Assert-True 'partial config: exit 0' ($RC -eq 0)
    Assert-Eq 'partial config: resolved' "BatchSize=200`nCompress=false`nEndpoint=http://127.0.0.1:9009/ingest`nFlushSeconds=10`nRegion=eu-1`n" $OUT

    # --- explicit arguments beat the config file ---
    Invoke-Tool @('-Config', $cfg, '-BatchSize', '75')
    Assert-Eq 'arg beats config: resolved' "BatchSize=75`nCompress=false`nEndpoint=http://127.0.0.1:9009/ingest`nFlushSeconds=10`nRegion=eu-1`n" $OUT

    # --- explicit arg with the same value as the default still wins over config ---
    Invoke-Tool @('-Config', $cfg, '-BatchSize', '50', '-Region', 'local')
    Assert-Eq 'arg same as default: resolved' $defaults $OUT

    # --- a switch given as -Compress:$false explicitly overrides config true ---
    $con = Write-Config 'compress.json' '{"Compress": true, "FlushSeconds": 30}'
    Invoke-Tool @('-Config', $con)
    Assert-Eq 'config compress: resolved' "BatchSize=50`nCompress=true`nEndpoint=http://127.0.0.1:9009/ingest`nFlushSeconds=30`nRegion=local`n" $OUT

    Invoke-Tool @('-Config', $con, '-Compress:$false')
    Assert-Eq 'explicit negation beats config: resolved' "BatchSize=50`nCompress=false`nEndpoint=http://127.0.0.1:9009/ingest`nFlushSeconds=30`nRegion=local`n" $OUT

    # --- -Compress alone, no config ---
    Invoke-Tool @('-Compress')
    Assert-Eq 'switch alone: resolved' "BatchSize=50`nCompress=true`nEndpoint=http://127.0.0.1:9009/ingest`nFlushSeconds=10`nRegion=local`n" $OUT

    # --- endpoint plumbs through both layers ---
    $e = Write-Config 'endpoint.json' '{"Endpoint": "https://collector.internal:4443/v2"}'
    Invoke-Tool @('-Config', $e)
    Assert-Eq 'config endpoint: resolved' "BatchSize=50`nCompress=false`nEndpoint=https://collector.internal:4443/v2`nFlushSeconds=10`nRegion=local`n" $OUT
    Invoke-Tool @('-Config', $e, '-Endpoint', 'http://127.0.0.1:9999/alt')
    Assert-Eq 'arg endpoint: resolved' "BatchSize=50`nCompress=false`nEndpoint=http://127.0.0.1:9999/alt`nFlushSeconds=10`nRegion=local`n" $OUT

    # --- config keys are case-sensitive: 'batchsize' is NOT a setting ---
    $bad = Write-Config 'casing.json' '{"batchsize": 5}'
    Invoke-Tool @('-Config', $bad)
    Assert-True 'lowercase key: exit 65' ($RC -eq 65)
    Assert-Eq 'lowercase key: stdout empty' '' $OUT
    Assert-Eq 'lowercase key: message' "effconf: unknown setting in config: batchsize`n" $ERR

    # --- two unknown keys: the ordinal-first one is reported ---
    $two = Write-Config 'two-unknown.json' '{"batchsize": 5, "Zone": "x"}'
    Invoke-Tool @('-Config', $two)
    Assert-True 'two unknown: exit 65' ($RC -eq 65)
    Assert-Eq 'two unknown: ordinal-first reported' "effconf: unknown setting in config: Zone`n" $ERR

    # --- config type errors ---
    $t1 = Write-Config 'strnum.json' '{"BatchSize": "200"}'
    Invoke-Tool @('-Config', $t1)
    Assert-True 'string batch: exit 65' ($RC -eq 65)
    Assert-Eq 'string batch: message' "effconf: setting BatchSize must be a whole number`n" $ERR

    $t2 = Write-Config 'fracnum.json' '{"FlushSeconds": 2.5}'
    Invoke-Tool @('-Config', $t2)
    Assert-True 'fractional flush: exit 65' ($RC -eq 65)
    Assert-Eq 'fractional flush: message' "effconf: setting FlushSeconds must be a whole number`n" $ERR

    $t3 = Write-Config 'strbool.json' '{"Compress": "yes"}'
    Invoke-Tool @('-Config', $t3)
    Assert-True 'string bool: exit 65' ($RC -eq 65)
    Assert-Eq 'string bool: message' "effconf: setting Compress must be true or false`n" $ERR

    $t4 = Write-Config 'numstr.json' '{"Region": 7}'
    Invoke-Tool @('-Config', $t4)
    Assert-True 'numeric region: exit 65' ($RC -eq 65)
    Assert-Eq 'numeric region: message' "effconf: setting Region must be a string`n" $ERR

    # --- type errors are reported ordinal-first by key too ---
    $t5 = Write-Config 'two-bad-types.json' '{"Compress": "yes", "BatchSize": "many"}'
    Invoke-Tool @('-Config', $t5)
    Assert-Eq 'two bad types: BatchSize first' "effconf: setting BatchSize must be a whole number`n" $ERR

    # --- resolved-value range checks, wherever the value came from ---
    $z = Write-Config 'zero.json' '{"BatchSize": 0}'
    Invoke-Tool @('-Config', $z)
    Assert-True 'config zero: exit 65' ($RC -eq 65)
    Assert-Eq 'config zero: message' "effconf: BatchSize out of range: 0`n" $ERR

    Invoke-Tool @('-BatchSize', '0')
    Assert-True 'arg zero: exit 65' ($RC -eq 65)
    Assert-Eq 'arg zero: message' "effconf: BatchSize out of range: 0`n" $ERR

    Invoke-Tool @('-FlushSeconds', '3601')
    Assert-True 'flush high: exit 65' ($RC -eq 65)
    Assert-Eq 'flush high: message' "effconf: FlushSeconds out of range: 3601`n" $ERR

    # --- config file problems ---
    $gone = Join-Path $T 'gone.json'
    Invoke-Tool @('-Config', $gone)
    Assert-True 'missing config: exit 65' ($RC -eq 65)
    Assert-Eq 'missing config: message' "effconf: config not found: $gone`n" $ERR

    $mal = Write-Config 'broken.json' '{"BatchSize": 200'
    Invoke-Tool @('-Config', $mal)
    Assert-True 'malformed: exit 65' ($RC -eq 65)
    Assert-Eq 'malformed: message' "effconf: config is not valid JSON: $mal`n" $ERR

    $arr = Write-Config 'array.json' '[1, 2, 3]'
    Invoke-Tool @('-Config', $arr)
    Assert-True 'array config: exit 65' ($RC -eq 65)
    Assert-Eq 'array config: message' "effconf: config must be a JSON object`n" $ERR

    # --- empty -Config value is a usage error ---
    Invoke-Tool @('-Config', '')
    Assert-True 'empty config path: exit 64' ($RC -eq 64)
    Assert-Eq 'empty config path: stdout empty' '' $OUT
    Assert-Eq 'empty config path: message' "effconf: -Config requires a non-empty path`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
