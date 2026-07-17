# Regression harness for hostswap.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_hostswap.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'hostswap.ps1') -PathType Leaf)) {
    Write-Output 'FAIL hostswap.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'hostswap.ps1') @CaseArgs 1>$outFile 2>$errFile
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

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- a key rename must not touch the legacy underscore alias ---
    $pool = Write-Fixture 'pool.conf' "db.pool.size=25`n# legacy alias, still read by the v1 agent`ndb_pool_size=25`ndb.pool.warm=5`n"
    Invoke-Tool @('-Path', $pool, '-From', 'db.pool.size', '-To', 'db.pool.max')
    Assert-True 'pool: exit 0' ($RC -eq 0)
    Assert-Eq 'pool: stderr empty' '' $ERR
    Assert-Eq 'pool: stdout' "hostswap: 1 replacement in $pool`n" $OUT
    Assert-Eq 'pool: file content' "db.pool.max=25`n# legacy alias, still read by the v1 agent`ndb_pool_size=25`ndb.pool.warm=5`n" ([System.IO.File]::ReadAllText($pool))

    # --- several hits across lines; the lookalike staging host stays ---
    $eps = Write-Fixture 'endpoints.conf' "primary=metrics.host.example:9009`nfallback=metrics.host.example:9010`n# staging is a different machine entirely`nstaging=metrics-host.example:9009`n"
    Invoke-Tool @('-Path', $eps, '-From', 'metrics.host.example', '-To', 'metrics.host.internal')
    Assert-True 'endpoints: exit 0' ($RC -eq 0)
    Assert-Eq 'endpoints: stdout' "hostswap: 2 replacements in $eps`n" $OUT
    Assert-Eq 'endpoints: file content' "primary=metrics.host.internal:9009`nfallback=metrics.host.internal:9010`n# staging is a different machine entirely`nstaging=metrics-host.example:9009`n" ([System.IO.File]::ReadAllText($eps))

    # --- zero hits: the file must come back byte-identical ---
    $cache = Write-Fixture 'cache.conf' "db-pool-size=9`nnote=leave this file alone`n"
    Invoke-Tool @('-Path', $cache, '-From', 'db.pool.size', '-To', 'db.pool.max')
    Assert-True 'nohit: exit 0' ($RC -eq 0)
    Assert-Eq 'nohit: stdout' "hostswap: 0 replacements in $cache`n" $OUT
    Assert-Eq 'nohit: file content' "db-pool-size=9`nnote=leave this file alone`n" ([System.IO.File]::ReadAllText($cache))

    # --- one-line file keeps its trailing newline ---
    $one = Write-Fixture 'one.conf' "host=old.example`n"
    Invoke-Tool @('-Path', $one, '-From', 'old.example', '-To', 'new.example')
    Assert-True 'oneline: exit 0' ($RC -eq 0)
    Assert-Eq 'oneline: stdout' "hostswap: 1 replacement in $one`n" $OUT
    Assert-Eq 'oneline: file content' "host=new.example`n" ([System.IO.File]::ReadAllText($one))

    # --- and a file with no trailing newline doesn't grow one ---
    $bare = Write-Fixture 'bare.conf' 'host=old.example'
    Invoke-Tool @('-Path', $bare, '-From', 'old.example', '-To', 'new.example')
    Assert-True 'bare: exit 0' ($RC -eq 0)
    Assert-Eq 'bare: file content' 'host=new.example' ([System.IO.File]::ReadAllText($bare))

    # --- missing file ---
    $gone = Join-Path $T 'gone.conf'
    Invoke-Tool @('-Path', $gone, '-From', 'a', '-To', 'b')
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "hostswap: file not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
