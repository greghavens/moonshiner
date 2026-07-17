# Regression harness for relayplan.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_relayplan.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'relayplan.ps1') -PathType Leaf)) {
    Write-Output 'FAIL relayplan.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'relayplan.ps1') @CaseArgs 1>$outFile 2>$errFile
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

$mainCsv = @'
host,stage,path
web01,canary,conf/nginx.toml
web01,canary,conf/tls.toml
db01,prod,conf/pg.toml
cache01,prod,conf/redis.toml
'@

$planExpected = @'
[plan] session-start 3 hosts
== host cache01 (stage prod) ==
[plan] copy conf/redis.toml -> cache01
== host db01 (stage prod) ==
[plan] copy conf/pg.toml -> db01
== host web01 (stage canary) ==
[plan] copy conf/nginx.toml -> web01
[plan] check conf/nginx.toml on web01
[plan] copy conf/tls.toml -> web01
[plan] check conf/tls.toml on web01
[plan] session-end 3 hosts

'@

$applyExpected = @'
[apply] session-start 3 hosts
== host cache01 (stage prod) ==
[apply] copy conf/redis.toml -> cache01
== host db01 (stage prod) ==
[apply] copy conf/pg.toml -> db01
== host web01 (stage canary) ==
[apply] copy conf/nginx.toml -> web01
[apply] check conf/nginx.toml on web01
[apply] copy conf/tls.toml -> web01
[apply] check conf/tls.toml on web01
[apply] session-end 3 hosts

'@

$loneCsv = @'
host,stage,path
edge07,canary,conf/edge.toml
'@

$loneApplyExpected = @'
[apply] session-start 1 hosts
== host edge07 (stage canary) ==
[apply] copy conf/edge.toml -> edge07
[apply] check conf/edge.toml on edge07
[apply] session-end 1 hosts

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- review run: every tagged line carries [plan] ---
    $main = Write-Fixture 'manifest.csv' $mainCsv
    Invoke-Tool @('-Path', $main)
    Assert-True 'plan: exit 0' ($RC -eq 0)
    Assert-Eq 'plan: stderr empty' '' $ERR
    Assert-Eq 'plan: output' $planExpected $OUT

    # --- apply run: every tagged line carries [apply] so the executor runs it ---
    Invoke-Tool @('-Path', $main, '-Apply')
    Assert-True 'apply: exit 0' ($RC -eq 0)
    Assert-Eq 'apply: stderr empty' '' $ERR
    Assert-Eq 'apply: output' $applyExpected $OUT

    # --- single canary host, apply run ---
    $lone = Write-Fixture 'lone.csv' $loneCsv
    Invoke-Tool @('-Path', $lone, '-Apply')
    Assert-True 'lone: exit 0' ($RC -eq 0)
    Assert-Eq 'lone: output' $loneApplyExpected $OUT

    # --- missing manifest ---
    $gone = Join-Path $T 'gone.csv'
    Invoke-Tool @('-Path', $gone)
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "relayplan: manifest not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
