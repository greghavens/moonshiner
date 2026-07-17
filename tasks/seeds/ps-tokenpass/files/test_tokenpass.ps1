# Acceptance harness for tokenpass.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_tokenpass.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$lib = Join-Path $PSScriptRoot 'tokenpass.ps1'
if (-not (Test-Path -LiteralPath $lib -PathType Leaf)) {
    Write-Output 'FAIL tokenpass.ps1 not found in the workspace root'
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

# Session state must live in the session value, not in script/module state.
$src = [System.IO.File]::ReadAllText($lib).ToLowerInvariant()
foreach ($banned in @('$script:', '$global:', 'new-variable')) {
    Assert-True "source does not use $banned" (-not $src.Contains($banned))
}

$driver = @'
param([string]$BaseUrl)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'tokenpass.ps1')

function Show-Log {
    param([string]$Label)
    $entries = Invoke-RestMethod -Uri "$BaseUrl/__log__"
    foreach ($e in @($entries)) {
        $auth = if ($null -eq $e.auth) { '-' } else { $e.auth }
        Write-Output "$Label.log $($e.method) $($e.path) $auth $($e.status)"
    }
    Invoke-RestMethod -Uri "$BaseUrl/__reset__" -Method Post > $null
}

function Show-Err {
    param([string]$Label, [scriptblock]$Body)
    try { $null = & $Body; Write-Output "$Label=[NO-ERROR]" }
    catch { Write-Output "$Label=[ERR $($_.Exception.Message)]" }
}

# s1: lazy login on first call, token reused afterwards
$alice = New-ApiSession -BaseUrl $BaseUrl -Account 'alice' -Secret 's-alfa'
Write-Output "s1.init=[$($alice.State)]"
Write-Output "s1.first=[$(Invoke-ApiGet -Session $alice -Path '/data/summary')]"
Write-Output "s1.second=[$(Invoke-ApiGet -Session $alice -Path '/data/summary')]"
Write-Output "s1.state=[$($alice.State)]"
Write-Output "s1.history=[$($alice.History -join '>')]"
Show-Log 's1'

# s2: token invalidated server-side -> exactly one re-login, then replay
Invoke-RestMethod -Uri "$BaseUrl/__expire__" -Method Post -ContentType 'application/json' -Body '{"account":"alice"}' > $null
Write-Output "s2.call=[$(Invoke-ApiGet -Session $alice -Path '/data/summary')]"
Write-Output "s2.state=[$($alice.State)]"
Write-Output "s2.history=[$($alice.History -join '>')]"
Show-Log 's2'

# s3: two sessions interleaved keep their own tokens
$bob = New-ApiSession -BaseUrl $BaseUrl -Account 'bob' -Secret 's-bravo'
Write-Output "s3.bob1=[$(Invoke-ApiGet -Session $bob -Path '/data/summary')]"
Write-Output "s3.alice=[$(Invoke-ApiGet -Session $alice -Path '/data/summary')]"
Write-Output "s3.bob2=[$(Invoke-ApiGet -Session $bob -Path '/data/summary')]"
Show-Log 's3'

# s4: still 401 after the one re-login -> rejected, and it stays rejected
Show-Err 's4.err' { Invoke-ApiGet -Session $alice -Path '/data/locked' }
Write-Output "s4.state=[$($alice.State)]"
Write-Output "s4.history=[$($alice.History -join '>')]"
Show-Err 's4.next' { Invoke-ApiGet -Session $alice -Path '/data/summary' }
Show-Log 's4'

# s5: login rejected outright
$carol = New-ApiSession -BaseUrl $BaseUrl -Account 'carol' -Secret 's-nope'
Show-Err 's5.err' { Invoke-ApiGet -Session $carol -Path '/data/summary' }
Write-Output "s5.state=[$($carol.State)]"
Write-Output "s5.history=[$($carol.History -join '>')]"
Show-Log 's5'

# s6: a 500 is not an auth problem — no re-login, session stays active
Show-Err 's6.err' { Invoke-ApiGet -Session $bob -Path '/data/oops' }
Write-Output "s6.state=[$($bob.State)]"
Write-Output "s6.history=[$($bob.History -join '>')]"
Show-Log 's6'
'@

$expected = @'
s1.init=[anonymous]
s1.first=[{"owner": "alice", "serial": 1}]
s1.second=[{"owner": "alice", "serial": 2}]
s1.state=[active]
s1.history=[anonymous>active]
s1.log POST /login - 200
s1.log GET /data/summary Bearer tok-alice-1 200
s1.log GET /data/summary Bearer tok-alice-1 200
s2.call=[{"owner": "alice", "serial": 3}]
s2.state=[active]
s2.history=[anonymous>active>renewing>active]
s2.log GET /data/summary Bearer tok-alice-1 401
s2.log POST /login - 200
s2.log GET /data/summary Bearer tok-alice-2 200
s3.bob1=[{"owner": "bob", "serial": 1}]
s3.alice=[{"owner": "alice", "serial": 4}]
s3.bob2=[{"owner": "bob", "serial": 2}]
s3.log POST /login - 200
s3.log GET /data/summary Bearer tok-bob-1 200
s3.log GET /data/summary Bearer tok-alice-2 200
s3.log GET /data/summary Bearer tok-bob-1 200
s4.err=[ERR tokenpass: still unauthorized after re-login]
s4.state=[rejected]
s4.history=[anonymous>active>renewing>active>renewing>active>rejected]
s4.next=[ERR tokenpass: session rejected]
s4.log GET /data/locked Bearer tok-alice-2 401
s4.log POST /login - 200
s4.log GET /data/locked Bearer tok-alice-3 401
s5.err=[ERR tokenpass: login rejected for account carol]
s5.state=[rejected]
s5.history=[anonymous>rejected]
s5.log POST /login - 403
s6.err=[ERR tokenpass: unexpected status 500 from /data/oops]
s6.state=[active]
s6.history=[anonymous>active]
s6.log GET /data/oops Bearer tok-bob-1 500

'@

$srv = $null
try {
    New-Item -ItemType Directory -Force -Path $T > $null
    $portFile = Join-Path $T 'port.txt'
    $srv = Start-Process -FilePath 'python3' `
        -ArgumentList @((Join-Path $PSScriptRoot 'mock_authapi.py'), $portFile) `
        -PassThru `
        -RedirectStandardOutput (Join-Path $T 'srv.out') `
        -RedirectStandardError (Join-Path $T 'srv.err')
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Path -LiteralPath $portFile)) {
        if ($srv.HasExited -or [DateTime]::UtcNow -gt $deadline) {
            throw "mock server failed to start: $(Get-Content -LiteralPath (Join-Path $T 'srv.err') -Raw -ErrorAction SilentlyContinue)"
        }
        Start-Sleep -Milliseconds 50
    }
    $port = [int](Get-Content -LiteralPath $portFile -Raw).Trim()

    $driverPath = Join-Path $T 'driver.ps1'
    [System.IO.File]::WriteAllText($driverPath, $driver)
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    & pwsh -NoProfile -NonInteractive -File $driverPath "http://127.0.0.1:$port" 1>$outFile 2>$errFile
    $rc = $LASTEXITCODE

    Assert-True 'driver exits 0' ($rc -eq 0)
    Assert-Eq 'driver stderr empty' '' ([System.IO.File]::ReadAllText($errFile))
    Assert-Eq 'scenario block' $expected ([System.IO.File]::ReadAllText($outFile))
} finally {
    if ($srv -and -not $srv.HasExited) { $srv.Kill(); $srv.WaitForExit() }
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
