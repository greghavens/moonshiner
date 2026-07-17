# Acceptance harness for matchbook.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_matchbook.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$lib = Join-Path $PSScriptRoot 'matchbook.ps1'
if (-not (Test-Path -LiteralPath $lib -PathType Leaf)) {
    Write-Output 'FAIL matchbook.ps1 not found in the workspace root'
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
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'matchbook.ps1')

function Out-Line { param([string]$Label, [string]$Value) Write-Output "$Label=[$Value]" }
function Out-Threw {
    param([string]$Label, [scriptblock]$Body)
    try { $null = & $Body; Write-Output "$Label=[NO-ERROR]" }
    catch { Write-Output "$Label=[THREW]" }
}
function Fmt-Note {
    param($r)
    if ($null -eq $r) { return 'NULL' }
    return '{0}|{1}|{2}|{3}|{4}' -f $r.Date, $r.Service, $r.Action, $r.WindowStart, $r.WindowEnd
}
function Fmt-Refs {
    param([string]$Text)
    $rs = @(Get-ChangeRefs -Text $Text)
    if ($rs.Count -eq 0) { return 'NONE' }
    return ($rs | ForEach-Object { '{0}@{1}#{2}' -f $_.Ref, $_.Index, $_.Number }) -join ' '
}

Out-Line 'note.restart' (Fmt-Note (Get-NoteFields -Line '2026-07-11 cache-primary restart window=03:00-03:30'))
Out-Line 'note.reload' (Fmt-Note (Get-NoteFields -Line '2026-07-12 dbpool reload window=22:00-22:15'))
Out-Line 'note.patch' (Fmt-Note (Get-NoteFields -Line '2026-07-13 edge-cdn-4 patch window=01:15-01:45'))
Out-Line 'note.upper.svc' (Fmt-Note (Get-NoteFields -Line '2026-07-11 CACHE-primary restart window=03:00-03:30'))
Out-Line 'note.upper.action' (Fmt-Note (Get-NoteFields -Line '2026-07-11 cache-primary RESTART window=03:00-03:30'))
Out-Line 'note.trailing' (Fmt-Note (Get-NoteFields -Line '2026-07-11 cache-primary restart window=03:00-03:30 extra'))
Out-Line 'note.leading' (Fmt-Note (Get-NoteFields -Line 'ok 2026-07-11 cache-primary restart window=03:00-03:30'))
Out-Line 'note.badaction' (Fmt-Note (Get-NoteFields -Line '2026-07-11 cache-primary reboot window=03:00-03:30'))
Out-Line 'note.badsvc' (Fmt-Note (Get-NoteFields -Line '2026-07-11 9cache restart window=03:00-03:30'))

Out-Line 'refs.inline' (Fmt-Refs 'CHG-12 chg-9 CHG-345')
Out-Line 'refs.multiline' (Fmt-Refs "window moved`nCHG-7 approved`nsee CHG-88 and chg-9")
Out-Line 'refs.none' (Fmt-Refs 'no refs here')
Out-Line 'refs.numtype' (@(Get-ChangeRefs -Text 'CHG-12')[0].Number.GetType().Name)

Out-Line 'mask.two' (Hide-OwnerAddresses -Text 'page dana.r@ops.example then b.wu@dc2.ops.example')
Out-Line 'mask.onechar' (Hide-OwnerAddresses -Text 'x@ops.example')
Out-Line 'mask.none' (Hide-OwnerAddresses -Text 'no owners here')

Out-Line 'fm.anywhere.sub' ([string](Test-FieldMatch -Value 'cache-prod' -Pattern 'cache' -Mode Anywhere))
Out-Line 'fm.exact.sub' ([string](Test-FieldMatch -Value 'cache-prod' -Pattern 'cache' -Mode Exact))
Out-Line 'fm.exact.full' ([string](Test-FieldMatch -Value 'cache' -Pattern 'cache' -Mode Exact))
Out-Line 'fm.case' ([string](Test-FieldMatch -Value 'Cache' -Pattern 'cache' -Mode Anywhere))
Out-Line 'fm.useranchor' ([string](Test-FieldMatch -Value 'db cache log' -Pattern '^cache$' -Mode Anywhere))
Out-Line 'fm.exact.dot' ([string](Test-FieldMatch -Value 'cache' -Pattern 'c.che' -Mode Exact))
Out-Line 'fm.anywhere.class' ([string](Test-FieldMatch -Value 'cache01' -Pattern '\d+' -Mode Anywhere))
Out-Line 'fm.exact.alt.a' ([string](Test-FieldMatch -Value 'cat' -Pattern 'cat|dog' -Mode Exact))
Out-Line 'fm.exact.alt.b' ([string](Test-FieldMatch -Value 'dog' -Pattern 'cat|dog' -Mode Exact))
Out-Line 'fm.exact.alt.spill' ([string](Test-FieldMatch -Value 'catx' -Pattern 'cat|dog' -Mode Exact))
Out-Threw 'fm.badmode' { Test-FieldMatch -Value 'x' -Pattern 'x' -Mode fuzzy }
'@

$expected = @'
note.restart=[2026-07-11|cache-primary|restart|03:00|03:30]
note.reload=[2026-07-12|dbpool|reload|22:00|22:15]
note.patch=[2026-07-13|edge-cdn-4|patch|01:15|01:45]
note.upper.svc=[NULL]
note.upper.action=[NULL]
note.trailing=[NULL]
note.leading=[NULL]
note.badaction=[NULL]
note.badsvc=[NULL]
refs.inline=[CHG-12@0#12 CHG-345@13#345]
refs.multiline=[CHG-7@13#7 CHG-88@32#88]
refs.none=[NONE]
refs.numtype=[Int32]
mask.two=[page d***@ops.example then b***@dc2.ops.example]
mask.onechar=[x***@ops.example]
mask.none=[no owners here]
fm.anywhere.sub=[True]
fm.exact.sub=[False]
fm.exact.full=[True]
fm.case=[False]
fm.useranchor=[False]
fm.exact.dot=[True]
fm.anywhere.class=[True]
fm.exact.alt.a=[True]
fm.exact.alt.b=[True]
fm.exact.alt.spill=[False]
fm.badmode=[THREW]

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
