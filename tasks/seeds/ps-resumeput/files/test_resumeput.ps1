# Acceptance harness for resumeput.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_resumeput.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$client = Join-Path $PSScriptRoot 'resumeput.ps1'
if (-not (Test-Path -LiteralPath $client -PathType Leaf)) {
    Write-Output 'FAIL resumeput.ps1 not found in the workspace root'
    exit 1
}

$T = Join-Path $PSScriptRoot '_t'
$script:checks = 0
$script:fails = 0
$script:srv = $null
$script:baseUrl = $null

$SHA10000 = '809cce0eb8545a303bcd5315525971feead212a557cbc9ec22ec8d910ac014e9'
$SHA9000 = 'b2c3059a974ce22411b4aaa3215aa6aec82cb1207f32f4bac9603d656fa76d4e'
$SHAEMPTY = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'

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

function New-Fixture {
    param([string]$Name, [int]$Size)
    $bytes = [byte[]]::new($Size)
    for ($i = 0; $i -lt $Size; $i++) {
        $bytes[$i] = [byte]((($i * $i) + 7) % 251)
    }
    $path = Join-Path $T $Name
    [System.IO.File]::WriteAllBytes($path, $bytes)
    $path
}

function Start-Depot {
    param([string[]]$ExtraArgs = @())
    Stop-Depot
    $portFile = Join-Path $T 'port.txt'
    Remove-Item -LiteralPath $portFile -ErrorAction SilentlyContinue
    $srvArgs = @((Join-Path $PSScriptRoot 'mock_depot.py'), $portFile) + $ExtraArgs
    $script:srv = Start-Process -FilePath 'python3' -ArgumentList $srvArgs -PassThru `
        -RedirectStandardOutput (Join-Path $T 'srv.out') `
        -RedirectStandardError (Join-Path $T 'srv.err')
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Path -LiteralPath $portFile)) {
        if ($script:srv.HasExited -or [DateTime]::UtcNow -gt $deadline) {
            throw "mock depot failed to start: $(Get-Content -LiteralPath (Join-Path $T 'srv.err') -Raw -ErrorAction SilentlyContinue)"
        }
        Start-Sleep -Milliseconds 50
    }
    $port = [int](Get-Content -LiteralPath $portFile -Raw).Trim()
    $script:baseUrl = "http://127.0.0.1:$port"
}

function Stop-Depot {
    if ($script:srv -and -not $script:srv.HasExited) {
        $script:srv.Kill()
        $script:srv.WaitForExit()
    }
    $script:srv = $null
}

function Invoke-Client {
    param([string]$Path, [int]$ChunkSize)
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    & pwsh -NoProfile -NonInteractive -File $client -BaseUrl $script:baseUrl `
        -Path $Path -ChunkSize $ChunkSize 1>$outFile 2>$errFile
    [pscustomobject]@{
        Code   = $LASTEXITCODE
        Stdout = [System.IO.File]::ReadAllText($outFile)
        Stderr = [System.IO.File]::ReadAllText($errFile)
    }
}

function Get-DepotState {
    $s = Invoke-RestMethod -Uri "$script:baseUrl/__state__"
    $puts = @($s.puts | ForEach-Object { "$($_.offset):$($_.len):$($_.result)" }) -join ','
    if ($puts -eq '') { $puts = '-' }
    $done = if ($s.completed) { 'true' } else { 'false' }
    "committed=$($s.committed) sha=$($s.sha256) completed=$done puts=$puts probes=$($s.probes)"
}

try {
    New-Item -ItemType Directory -Force -Path $T > $null
    $big = New-Fixture 'payload.bin' 10000
    $even = New-Fixture 'even.bin' 9000
    $empty = New-Fixture 'empty.bin' 0

    # --- scripted 500 on the 3rd chunk, its bytes discarded: probe, resend, finish
    Start-Depot -ExtraArgs @('--fail-put', '3')
    $r = Invoke-Client -Path $big -ChunkSize 3000
    Assert-True 'drop: exit code 0' ($r.Code -eq 0)
    Assert-Eq 'drop: stderr empty' '' $r.Stderr
    Assert-Eq 'drop: summary' ('{"id":"u1","bytes":10000,"puts":5,"probes":1,"sha256":"' + $SHA10000 + '"}' + "`n") $r.Stdout
    Assert-Eq 'drop: depot state' "committed=10000 sha=$SHA10000 completed=true puts=0:3000:ok,3000:3000:ok,6000:3000:500-drop,6000:3000:ok,9000:1000:ok probes=1" (Get-DepotState)

    # --- scripted 500 on the 3rd chunk AFTER the depot kept the bytes:
    #     the probed offset already includes chunk 3, so it is never resent
    Start-Depot -ExtraArgs @('--break-put', '3')
    $r = Invoke-Client -Path $big -ChunkSize 3000
    Assert-True 'kept: exit code 0' ($r.Code -eq 0)
    Assert-Eq 'kept: summary' ('{"id":"u1","bytes":10000,"puts":4,"probes":1,"sha256":"' + $SHA10000 + '"}' + "`n") $r.Stdout
    Assert-Eq 'kept: depot state' "committed=10000 sha=$SHA10000 completed=true puts=0:3000:ok,3000:3000:ok,6000:3000:500-kept,9000:1000:ok probes=1" (Get-DepotState)

    # --- clean run, size an exact multiple of the chunk size
    Start-Depot
    $r = Invoke-Client -Path $even -ChunkSize 3000
    Assert-True 'even: exit code 0' ($r.Code -eq 0)
    Assert-Eq 'even: summary' ('{"id":"u1","bytes":9000,"puts":3,"probes":0,"sha256":"' + $SHA9000 + '"}' + "`n") $r.Stdout
    Assert-Eq 'even: depot state' "committed=9000 sha=$SHA9000 completed=true puts=0:3000:ok,3000:3000:ok,6000:3000:ok probes=0" (Get-DepotState)

    # --- empty file: no chunks at all, still created and completed
    Start-Depot
    $r = Invoke-Client -Path $empty -ChunkSize 3000
    Assert-True 'empty: exit code 0' ($r.Code -eq 0)
    Assert-Eq 'empty: stderr empty' '' $r.Stderr
    Assert-Eq 'empty: summary' ('{"id":"u1","bytes":0,"puts":0,"probes":0,"sha256":"' + $SHAEMPTY + '"}' + "`n") $r.Stdout
    Assert-Eq 'empty: depot state' "committed=0 sha=$SHAEMPTY completed=true puts=- probes=0" (Get-DepotState)
} finally {
    Stop-Depot
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
