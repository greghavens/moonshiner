# Acceptance harness for strkit.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_strkit.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$lib = Join-Path $PSScriptRoot 'strkit.ps1'
if (-not (Test-Path -LiteralPath $lib -PathType Leaf)) {
    Write-Output 'FAIL strkit.ps1 not found in the workspace root'
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

# The helper layer bans regex and expression evaluation by convention; the
# source itself is part of the contract.
$src = [System.IO.File]::ReadAllText($lib).ToLowerInvariant()
foreach ($banned in @('-match', '-cmatch', '-imatch', '-replace', '-creplace',
        '-ireplace', '-split', '-csplit', '[regex]', 'select-string',
        'invoke-expression')) {
    Assert-True "source does not use $banned" (-not $src.Contains($banned))
}

# All behavior checks run inside ONE child pwsh that dot-sources the library
# and prints a labeled line per call; the block is compared byte-exact.
$driver = @'
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'strkit.ps1')

function Out-Line { param([string]$Label, [string]$Value) Write-Output "$Label=[$Value]" }
function Out-Err {
    param([string]$Label, [scriptblock]$Body)
    try { $null = & $Body; Write-Output "$Label=[NO-ERROR]" }
    catch { Write-Output "$Label=[ERR $($_.Exception.Message)]" }
}
function Out-Threw {
    param([string]$Label, [scriptblock]$Body)
    try { $null = & $Body; Write-Output "$Label=[NO-ERROR]" }
    catch { Write-Output "$Label=[THREW]" }
}

Out-Line 'trim.ws' (Format-TrimText -Text '  spooler idle   ')
Out-Line 'trim.none' (Format-TrimText -Text 'clean')
Out-Line 'trim.empty' (Format-TrimText -Text '')
Out-Line 'trim.tab' (Format-TrimText -Text "`t hi `t")
Out-Line 'trim.chars' (Format-TrimText -Text '--=report=--' -Chars '-=')
Out-Line 'trim.chars.inner' (Format-TrimText -Text '..a.b..' -Chars '.')

Out-Line 'pad.right' (Format-PadText -Text 'ab' -Width 5 -Align right -PadChar '*')
Out-Line 'pad.left' (Format-PadText -Text 'ab' -Width 5 -Align left)
Out-Line 'pad.center' (Format-PadText -Text 'ab' -Width 7 -Align center -PadChar '.')
Out-Line 'pad.center.even' (Format-PadText -Text 'ab' -Width 6 -Align center -PadChar '.')
Out-Line 'pad.exact' (Format-PadText -Text 'abcde' -Width 5 -Align left)
Out-Line 'pad.over' (Format-PadText -Text 'abcdef' -Width 4 -Align right)
Out-Line 'pad.empty' (Format-PadText -Text '' -Width 3 -Align left -PadChar '_')
Out-Line 'pad.nonascii' (Format-PadText -Text 'étage' -Width 8 -Align left -PadChar '.')

Out-Line 'clip.fit' (Format-ClipText -Text 'warehouse' -Width 9)
Out-Line 'clip.long' (Format-ClipText -Text 'warehouse' -Width 6)
Out-Line 'clip.edge' (Format-ClipText -Text 'warehouse' -Width 3)
Out-Line 'clip.tiny' (Format-ClipText -Text 'warehouse' -Width 2)
Out-Line 'clip.one' (Format-ClipText -Text 'warehouse' -Width 1)
Out-Line 'clip.marker' (Format-ClipText -Text 'warehouse' -Width 6 -Marker ([string][char]0x2026))
Out-Line 'clip.shortmarker' (Format-ClipText -Text 'warehouse' -Width 3 -Marker '..')
Out-Line 'clip.empty' (Format-ClipText -Text '' -Width 4)
Out-Line 'clip.nonascii' (Format-ClipText -Text ('m' + [char]0xE9 + 'trage report') -Width 5)

Out-Line 'up.ascii' (ConvertTo-UpperText -Text 'cache pool')
Out-Line 'up.sharp' (ConvertTo-UpperText -Text 'straße')
Out-Line 'up.dotless' (ConvertTo-UpperText -Text 'ıspanak')
Out-Line 'lo.greek' (ConvertTo-LowerText -Text 'ΟΔΟΣ ΣΟΦΙΑΣ')
Out-Line 'lo.dotted' (ConvertTo-LowerText -Text 'İSTANBUL')
Out-Line 'lo.sharp' (ConvertTo-LowerText -Text 'WEIß')
Out-Line 'title.caps' (ConvertTo-TitleText -Text 'the NASA report')
Out-Line 'title.digit' (ConvertTo-TitleText -Text '3rd shift crew')
Out-Line 'title.accent' (ConvertTo-TitleText -Text 'école du nord')
Out-Line 'title.dotless' (ConvertTo-TitleText -Text 'ıspanak plan')
Out-Line 'title.hyphen' (ConvertTo-TitleText -Text 'mixed-CASE hyphen-words')

Out-Line 'stat.basic' (Format-StatLine -Label 'p95 latency' -Value 3.14159)
Out-Line 'stat.group' (Format-StatLine -Label 'queue depth (avg)' -Value 1234567.891)
Out-Line 'stat.overflow' (Format-StatLine -Label 'very long label overflowing' -Value 2.5)
Out-Line 'stat.round' (Format-StatLine -Label 'rounding' -Value 2.675)
Out-Line 'count.big' (Format-CountLine -Label 'events' -Count 1234567)
Out-Line 'count.zero' (Format-CountLine -Label 'drops' -Count 0)

Out-Err 'err.padchar.two' { Format-PadText -Text 'x' -Width 3 -Align left -PadChar 'ab' }
Out-Err 'err.padchar.empty' { Format-PadText -Text 'x' -Width 3 -Align left -PadChar '' }
Out-Err 'err.padwidth' { Format-PadText -Text 'x' -Width -1 -Align left }
Out-Err 'err.clipwidth' { Format-ClipText -Text 'x' -Width 0 }
Out-Threw 'err.align' { Format-PadText -Text 'x' -Width 3 -Align middle }
'@

$expected = @'
trim.ws=[spooler idle]
trim.none=[clean]
trim.empty=[]
trim.tab=[hi]
trim.chars=[report]
trim.chars.inner=[a.b]
pad.right=[***ab]
pad.left=[ab   ]
pad.center=[..ab...]
pad.center.even=[..ab..]
pad.exact=[abcde]
pad.over=[abcdef]
pad.empty=[___]
pad.nonascii=[étage...]
clip.fit=[warehouse]
clip.long=[war...]
clip.edge=[...]
clip.tiny=[..]
clip.one=[.]
clip.marker=[wareh…]
clip.shortmarker=[w..]
clip.empty=[]
clip.nonascii=[mé...]
up.ascii=[CACHE POOL]
up.sharp=[STRAßE]
up.dotless=[ıSPANAK]
lo.greek=[οδοσ σοφιασ]
lo.dotted=[İstanbul]
lo.sharp=[weiß]
title.caps=[The Nasa Report]
title.digit=[3Rd Shift Crew]
title.accent=[École Du Nord]
title.dotless=[ıspanak Plan]
title.hyphen=[Mixed-Case Hyphen-Words]
stat.basic=[p95 latency                  3.14]
stat.group=[queue depth (avg)    1,234,567.89]
stat.overflow=[very long label overflowing         2.50]
stat.round=[rounding                     2.67]
count.big=[events: 1,234,567]
count.zero=[drops: 0]
err.padchar.two=[ERR strkit: PadChar must be exactly one character]
err.padchar.empty=[ERR strkit: PadChar must be exactly one character]
err.padwidth=[ERR strkit: Width must be non-negative]
err.clipwidth=[ERR strkit: Width must be at least 1]
err.align=[THREW]

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
