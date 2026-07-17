# Acceptance harness for rpcbatch.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_rpcbatch.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$lib = Join-Path $PSScriptRoot 'rpcbatch.ps1'
if (-not (Test-Path -LiteralPath $lib -PathType Leaf)) {
    Write-Output 'FAIL rpcbatch.ps1 not found in the workspace root'
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

$driver = @'
param([string]$BaseUrl)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'rpcbatch.ps1')

$uri = "$BaseUrl/rpc"

function Show-Results {
    param([string]$Label, [object[]]$Results)
    $i = 0
    foreach ($r in $Results) {
        $i++
        switch ($r.Kind) {
            'ok'           { Write-Output "$Label$i=[ok $($r.Value)]" }
            'error'        { Write-Output "$Label$i=[error $($r.Code) $($r.Message)]" }
            'notification' { Write-Output "$Label$i=[notification]" }
            default        { Write-Output "$Label$i=[UNKNOWN-KIND $($r.Kind)]" }
        }
    }
}

function Show-Err {
    param([string]$Label, [scriptblock]$Body)
    try { $null = & $Body; Write-Output "$Label=[NO-ERROR]" }
    catch { Write-Output "$Label=[ERR $($_.Exception.Message)]" }
}

# a: values, a notification, an error object, an unknown method — order preserved
$callsA = @(
    @{ Method = 'sum';   Params = @(2, 3, 4) },
    @{ Method = 'log';   Params = @('heartbeat'); Notification = $true },
    @{ Method = 'upper'; Params = @('mixed Case') },
    @{ Method = 'fail' },
    @{ Method = 'nope' }
)
Show-Results 'a' (Invoke-RpcBatch -Uri $uri -Calls $callsA)

# b: all notifications — server answers 204 with no body
$callsB = @(
    @{ Method = 'note'; Params = @('a'); Notification = $true },
    @{ Method = 'note'; Params = @('b'); Notification = $true }
)
Show-Results 'b' (Invoke-RpcBatch -Uri $uri -Calls $callsB)

# c: server loses one response
Show-Err 'c' { Invoke-RpcBatch -Uri $uri -Calls @(
    @{ Method = 'sum'; Params = @(1, 2) },
    @{ Method = 'drop' },
    @{ Method = 'sum'; Params = @(5, 5) }
) }

# d: server answers with an id nobody asked for
Show-Err 'd' { Invoke-RpcBatch -Uri $uri -Calls @(
    @{ Method = 'sum'; Params = @(7) },
    @{ Method = 'stray' }
) }

# e: server answers the same id twice (also: a one-call batch is still an array)
Show-Err 'e' { Invoke-RpcBatch -Uri $uri -Calls @(
    @{ Method = 'dupe' }
) }

# f: empty batch never touches the wire
Show-Err 'f' { Invoke-RpcBatch -Uri $uri -Calls @() }
'@

$expected = @'
a1=[ok 9]
a2=[notification]
a3=[ok MIXED CASE]
a4=[error -32050 scripted failure]
a5=[error -32601 method not found]
b1=[notification]
b2=[notification]
c=[ERR rpcbatch: missing response for id 2]
d=[ERR rpcbatch: unexpected response id 9999]
e=[ERR rpcbatch: duplicate response id 1]
f=[ERR rpcbatch: empty batch]

'@

$expectedLog = @'
req1.1 v=2.0 id=1 method=sum params=[2,3,4]
req1.2 v=2.0 id=- method=log params=["heartbeat"]
req1.3 v=2.0 id=2 method=upper params=["mixed Case"]
req1.4 v=2.0 id=3 method=fail params=-
req1.5 v=2.0 id=4 method=nope params=-
req2.1 v=2.0 id=- method=note params=["a"]
req2.2 v=2.0 id=- method=note params=["b"]
req3.1 v=2.0 id=1 method=sum params=[1,2]
req3.2 v=2.0 id=2 method=drop params=-
req3.3 v=2.0 id=3 method=sum params=[5,5]
req4.1 v=2.0 id=1 method=sum params=[7]
req4.2 v=2.0 id=2 method=stray params=-
req5.1 v=2.0 id=1 method=dupe params=-
'@

$srv = $null
try {
    New-Item -ItemType Directory -Force -Path $T > $null
    $portFile = Join-Path $T 'port.txt'
    $srv = Start-Process -FilePath 'python3' `
        -ArgumentList @((Join-Path $PSScriptRoot 'mock_rpc.py'), $portFile) `
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
    $baseUrl = "http://127.0.0.1:$port"

    $driverPath = Join-Path $T 'driver.ps1'
    [System.IO.File]::WriteAllText($driverPath, $driver)
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    & pwsh -NoProfile -NonInteractive -File $driverPath $baseUrl 1>$outFile 2>$errFile
    $rc = $LASTEXITCODE

    Assert-True 'driver exits 0' ($rc -eq 0)
    Assert-Eq 'driver stderr empty' '' ([System.IO.File]::ReadAllText($errFile))
    Assert-Eq 'result block' $expected ([System.IO.File]::ReadAllText($outFile))

    # what actually went over the wire: ids assigned 1..n across the
    # non-notification calls, jsonrpc pinned, params passed through verbatim
    $log = Invoke-RestMethod -Uri "$baseUrl/__log__"
    $lines = [System.Collections.Generic.List[string]]::new()
    $ri = 0
    foreach ($req in @($log)) {
        $ri++
        $ei = 0
        foreach ($e in @($req)) {
            $ei++
            $v = if ($e.PSObject.Properties['jsonrpc']) { [string]$e.jsonrpc } else { '?' }
            $id = if ($e.PSObject.Properties['id']) { [string]$e.id } else { '-' }
            $m = if ($e.PSObject.Properties['method']) { [string]$e.method } else { '?' }
            $p = if ($e.PSObject.Properties['params']) {
                ConvertTo-Json -InputObject $e.params -Compress -Depth 5
            } else { '-' }
            $lines.Add("req$ri.$ei v=$v id=$id method=$m params=$p")
        }
    }
    Assert-Eq 'wire log' $expectedLog (@($lines) -join "`n")
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
