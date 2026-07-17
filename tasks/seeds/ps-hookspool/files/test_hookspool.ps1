# Acceptance harness for hookspool.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_hookspool.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$receiver = Join-Path $PSScriptRoot 'hookspool.ps1'
if (-not (Test-Path -LiteralPath $receiver -PathType Leaf)) {
    Write-Output 'FAIL hookspool.ps1 not found in the workspace root'
    exit 1
}

$T = Join-Path $PSScriptRoot '_t'
$script:checks = 0
$script:fails = 0
$SECRET = 'cedar-vault-042'

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

$expectedSends = @'
1 200
2 200
3 401
4 401
5 401
6 200
7 405
8 404
9 200

'@

$expectedSpool = @'
{"id":"evt-001","event":"disk.usage","host":"nas-2","pct":91}
{"id":"evt-002","event":"ops.note","msg":"café réunion"}
{"id":"evt-004","event":"fan.speed","host":"nas-2","rpm":4100}
{"id":"evt-003","event":"disk.usage","host":"nas-2","pct":88}

'@

$expectedManifest = '{"received":9,"accepted":4,"rejected":3,"other":2,"acceptedIds":["evt-001","evt-002","evt-004","evt-003"],"rejectedReasons":["bad-signature","missing-signature","bad-signature"]}'

$proc = $null
try {
    New-Item -ItemType Directory -Force -Path $T > $null
    $spool = Join-Path $T 'events.ndjson'
    $manifest = Join-Path $T 'manifest.json'
    $ready = Join-Path $T 'ready'

    # pick a free loopback port, then hand it to the receiver
    $probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $probe.Start()
    $port = ([System.Net.IPEndPoint]$probe.LocalEndpoint).Port
    $probe.Stop()

    $recvOut = Join-Path $T 'recv.out'
    $recvErr = Join-Path $T 'recv.err'
    $proc = Start-Process -FilePath 'pwsh' -ArgumentList @(
        '-NoProfile', '-NonInteractive', '-File', $receiver,
        '-Port', "$port", '-Secret', $SECRET, '-Count', '9',
        '-SpoolPath', $spool, '-ManifestPath', $manifest, '-ReadyPath', $ready
    ) -PassThru -RedirectStandardOutput $recvOut -RedirectStandardError $recvErr

    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while (-not (Test-Path -LiteralPath $ready)) {
        if ($proc.HasExited) {
            throw "receiver exited before signalling ready: $(Get-Content -LiteralPath $recvErr -Raw -ErrorAction SilentlyContinue)"
        }
        if ([DateTime]::UtcNow -gt $deadline) { throw 'receiver never signalled ready' }
        Start-Sleep -Milliseconds 50
    }

    $sendOut = Join-Path $T 'send.out'
    $sendErr = Join-Path $T 'send.err'
    & python3 (Join-Path $PSScriptRoot 'send_hooks.py') "http://127.0.0.1:$port" $SECRET 1>$sendOut 2>$sendErr
    Assert-True 'sender exits 0' ($LASTEXITCODE -eq 0)
    Assert-Eq 'sender stderr empty' '' ([System.IO.File]::ReadAllText($sendErr))

    if (-not $proc.WaitForExit(20000)) {
        $proc.Kill()
        $proc.WaitForExit()
        Assert-True 'receiver exits after -Count requests' $false
    } else {
        Assert-True 'receiver exit code 0' ($proc.ExitCode -eq 0)
    }
    Assert-Eq 'receiver stderr empty' '' ([System.IO.File]::ReadAllText($recvErr))

    Assert-Eq 'response codes seen by the sender' $expectedSends ([System.IO.File]::ReadAllText($sendOut))

    # spool comparison is over raw bytes: UTF-8, no BOM, one event per line
    Assert-True 'spool file exists' (Test-Path -LiteralPath $spool -PathType Leaf)
    $spoolText = [System.Text.Encoding]::UTF8.GetString([System.IO.File]::ReadAllBytes($spool))
    Assert-Eq 'spooled events' $expectedSpool $spoolText

    Assert-True 'manifest file exists' (Test-Path -LiteralPath $manifest -PathType Leaf)
    $manifestText = [System.Text.Encoding]::UTF8.GetString([System.IO.File]::ReadAllBytes($manifest))
    Assert-Eq 'manifest' $expectedManifest $manifestText
} finally {
    if ($proc -and -not $proc.HasExited) { $proc.Kill(); $proc.WaitForExit() }
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
