# Acceptance harness for shapekit.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_shapekit.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$lib = Join-Path $PSScriptRoot 'shapekit.ps1'
if (-not (Test-Path -LiteralPath $lib -PathType Leaf)) {
    Write-Output 'FAIL shapekit.ps1 not found in the workspace root'
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

# All behavior checks run inside ONE child pwsh that dot-sources the library
# UNDER STRICT MODE — reading a property that might not exist must not throw.
$driver = @'
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'shapekit.ps1')

function Out-Line { param([string]$Label, [string]$Value) Write-Output "$Label=[$Value]" }
function Out-Err {
    param([string]$Label, [scriptblock]$Body)
    try { $null = & $Body; Write-Output "$Label=[NO-ERROR]" }
    catch { Write-Output "$Label=[ERR $($_.Exception.Message)]" }
}

$full = [pscustomobject]@{
    hostName      = 'zrh-kiosk-2'
    siteCode      = 'zrh'
    uptimeMinutes = 3000
    errorCount    = 0
    note          = 'door sensor flaky'
}
$card = ConvertTo-KioskCard -Record $full
Out-Line 'card.props' ($card.PSObject.Properties.Name -join ',')
Out-Line 'card.render' ('{0}|{1}|{2}|{3}|{4}' -f $card.Kiosk, $card.Site, $card.Uptime, $card.Health, $card.Note)
Out-Line 'card.ispso' ($card -is [System.Management.Automation.PSCustomObject])

$noNote = [pscustomobject]@{ hostName = 'ams-kiosk-7'; siteCode = 'ams'; uptimeMinutes = 59; errorCount = 5 }
$c2 = ConvertTo-KioskCard -Record $noNote
Out-Line 'card.nonote' $c2.Note
Out-Line 'card.uptime.zero' $c2.Uptime
Out-Line 'card.health.five' $c2.Health

$jsonRec = '{"hostName": "fra-kiosk-9", "siteCode": "fra", "uptimeMinutes": 90, "errorCount": 6, "note": null}' | ConvertFrom-Json
$c3 = ConvertTo-KioskCard -Record $jsonRec
Out-Line 'card.jsonrender' ('{0}|{1}|{2}|{3}|{4}' -f $c3.Kiosk, $c3.Site, $c3.Uptime, $c3.Health, $c3.Note)

$emptyNote = [pscustomobject]@{ hostName = 'fra-kiosk-2'; siteCode = 'fra'; uptimeMinutes = 1440; errorCount = 1; note = '' }
$c4 = ConvertTo-KioskCard -Record $emptyNote
Out-Line 'card.emptynote' $c4.Note
Out-Line 'card.uptime.day' $c4.Uptime
Out-Line 'card.health.one' $c4.Health

$strMinutes = [pscustomobject]@{ hostName = 'ams-kiosk-1'; siteCode = 'ams'; uptimeMinutes = '180'; errorCount = 0 }
Out-Line 'card.uptime.strdigits' (ConvertTo-KioskCard -Record $strMinutes).Uptime

Out-Err 'card.err.nouptime' { ConvertTo-KioskCard -Record ([pscustomobject]@{ hostName = 'x'; siteCode = 'y'; errorCount = 1 }) }
Out-Err 'card.err.order' { ConvertTo-KioskCard -Record ([pscustomobject]@{ note = 'orphan row' }) }

$slim = Select-CardField -Card $card -Name Health, Kiosk
Out-Line 'sel.props' ($slim.PSObject.Properties.Name -join ',')
Out-Line 'sel.render' ('{0}|{1}' -f $slim.Health, $slim.Kiosk)
Out-Line 'sel.ispso' ($slim -is [System.Management.Automation.PSCustomObject])
Out-Err 'sel.err' { Select-CardField -Card $card -Name Kiosk, Rack }

$mixed = @(
    [pscustomobject]@{ hostName = 'a'; note = 'x' },
    $null,
    [pscustomobject]@{ hostName = 'b' },
    [pscustomobject]@{ hostName = 'c'; note = 'y' }
)
$cov = Measure-FieldCoverage -Records $mixed -Name note
Out-Line 'cov.props' ($cov.PSObject.Properties.Name -join ',')
Out-Line 'cov.render' ('{0}|{1}|{2}' -f $cov.Field, $cov.Present, $cov.Missing)
'@

$expected = @'
card.props=[Kiosk,Site,Uptime,Health,Note]
card.render=[zrh-kiosk-2|zrh|2d2h|ok|door sensor flaky]
card.ispso=[True]
card.nonote=[(none)]
card.uptime.zero=[0d0h]
card.health.five=[watch]
card.jsonrender=[fra-kiosk-9|fra|0d1h|fault|(none)]
card.emptynote=[]
card.uptime.day=[1d0h]
card.health.one=[watch]
card.uptime.strdigits=[0d3h]
card.err.nouptime=[ERR shapekit: record is missing required property 'uptimeMinutes']
card.err.order=[ERR shapekit: record is missing required property 'hostName']
sel.props=[Health,Kiosk]
sel.render=[ok|zrh-kiosk-2]
sel.ispso=[True]
sel.err=[ERR shapekit: unknown field 'Rack']
cov.props=[Field,Present,Missing]
cov.render=[note|2|1]

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null
    $driverPath = Join-Path $T 'driver.ps1'
    [System.IO.File]::WriteAllText($driverPath, $driver)
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    & pwsh -NoProfile -NonInteractive -File $driverPath 1>$outFile 2>$errFile
    $rc = $LASTEXITCODE
    $out = [System.IO.File]::ReadAllText($outFile)
    $err = [System.IO.File]::ReadAllText($errFile)

    Assert-True 'driver exits 0' ($rc -eq 0)
    Assert-Eq 'driver stderr empty' '' $err
    Assert-Eq 'behavior block' $expected $out
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
