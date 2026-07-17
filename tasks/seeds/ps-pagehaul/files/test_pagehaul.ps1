# Acceptance harness for pagehaul.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_pagehaul.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$client = Join-Path $PSScriptRoot 'pagehaul.ps1'
if (-not (Test-Path -LiteralPath $client -PathType Leaf)) {
    Write-Output 'FAIL pagehaul.ps1 not found in the workspace root'
    exit 1
}

$T = Join-Path $PSScriptRoot '_t'
$script:checks = 0
$script:fails = 0
$script:srv = $null
$script:baseUrl = $null

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

function Start-MockServer {
    param([string]$Dataset)
    Stop-MockServer
    $portFile = Join-Path $T 'port.txt'
    Remove-Item -LiteralPath $portFile -ErrorAction SilentlyContinue
    $script:srv = Start-Process -FilePath 'python3' `
        -ArgumentList @((Join-Path $PSScriptRoot 'mock_inventory.py'), $portFile, $Dataset) `
        -PassThru `
        -RedirectStandardOutput (Join-Path $T 'srv.out') `
        -RedirectStandardError (Join-Path $T 'srv.err')
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Path -LiteralPath $portFile)) {
        if ($script:srv.HasExited -or [DateTime]::UtcNow -gt $deadline) {
            throw "mock server failed to start: $(Get-Content -LiteralPath (Join-Path $T 'srv.err') -Raw -ErrorAction SilentlyContinue)"
        }
        Start-Sleep -Milliseconds 50
    }
    $port = [int](Get-Content -LiteralPath $portFile -Raw).Trim()
    $script:baseUrl = "http://127.0.0.1:$port"
}

function Stop-MockServer {
    if ($script:srv -and -not $script:srv.HasExited) {
        $script:srv.Kill()
        $script:srv.WaitForExit()
    }
    $script:srv = $null
}

function Get-ServerLog {
    $entries = Invoke-RestMethod -Uri "$script:baseUrl/__log__"
    $lines = foreach ($e in $entries) {
        $c = if ($null -eq $e.cursor) { '-' } else { $e.cursor }
        $l = if ($null -eq $e.limit) { '-' } else { $e.limit }
        "$c $l"
    }
    ($lines -join "`n")
}

function Reset-ServerLog {
    Invoke-RestMethod -Uri "$script:baseUrl/__reset__" -Method Post > $null
}

function Invoke-Client {
    param([int]$PageSize)
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    & pwsh -NoProfile -NonInteractive -File $client -BaseUrl $script:baseUrl -PageSize $PageSize 1>$outFile 2>$errFile
    [pscustomobject]@{
        Code   = $LASTEXITCODE
        Stdout = [System.IO.File]::ReadAllText($outFile)
        Stderr = [System.IO.File]::ReadAllText($errFile)
    }
}

$mainReport = '{"total":15,"pages":PAGES,"sites":[{"site":"Web","count":2},{"site":"cache","count":2},{"site":"db","count":3},{"site":"edge","count":3},{"site":"web","count":5}],"ids":["a-101","a-102","a-103","a-104","a-105","a-106","a-107","a-108","a-109","a-110","a-111","a-112","a-113","a-114","a-115"]}'

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- full dataset, page size 5: three full pages then the empty terminal page
    Start-MockServer -Dataset 'main'
    $r = Invoke-Client -PageSize 5
    Assert-True 'ps5: exit code 0' ($r.Code -eq 0)
    Assert-Eq 'ps5: stderr empty' '' $r.Stderr
    Assert-Eq 'ps5: report' (($mainReport -creplace 'PAGES', '4') + "`n") $r.Stdout
    Assert-Eq 'ps5: request log' "- 5`nt0180 5`nt02ed 5`nt045a 5" (Get-ServerLog)

    # --- page size 4: last page is partial, so no extra terminal request
    Reset-ServerLog
    $r = Invoke-Client -PageSize 4
    Assert-True 'ps4: exit code 0' ($r.Code -eq 0)
    Assert-Eq 'ps4: report' (($mainReport -creplace 'PAGES', '4') + "`n") $r.Stdout
    Assert-Eq 'ps4: request log' "- 4`nt0137 4`nt025b 4`nt037f 4" (Get-ServerLog)

    # --- page size larger than the dataset: exactly one request
    Reset-ServerLog
    $r = Invoke-Client -PageSize 40
    Assert-True 'ps40: exit code 0' ($r.Code -eq 0)
    Assert-Eq 'ps40: report' (($mainReport -creplace 'PAGES', '1') + "`n") $r.Stdout
    Assert-Eq 'ps40: request log' '- 40' (Get-ServerLog)

    # --- empty dataset: first page is already the terminal empty page
    Start-MockServer -Dataset 'empty'
    $r = Invoke-Client -PageSize 5
    Assert-True 'empty: exit code 0' ($r.Code -eq 0)
    Assert-Eq 'empty: stderr empty' '' $r.Stderr
    Assert-Eq 'empty: report' ('{"total":0,"pages":1,"sites":[],"ids":[]}' + "`n") $r.Stdout
    Assert-Eq 'empty: request log' '- 5' (Get-ServerLog)
} finally {
    Stop-MockServer
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
