# Acceptance harness for rotaplan.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_rotaplan.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$tool = Join-Path $PSScriptRoot 'rotaplan.ps1'
if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
    Write-Output 'FAIL rotaplan.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'rotaplan.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function Set-Mtime {
    param([string]$Path, [string]$Iso)
    $styles = [System.Globalization.DateTimeStyles]::AssumeUniversal -bor [System.Globalization.DateTimeStyles]::AdjustToUniversal
    $d = [datetime]::Parse($Iso, [System.Globalization.CultureInfo]::InvariantCulture, $styles)
    [System.IO.File]::SetLastWriteTimeUtc($Path, $d)
}

function New-LogFix {
    param([string]$Dir, [string]$Name, [string]$Text, [string]$MtimeIso)
    $p = Join-Path $Dir $Name
    [System.IO.File]::WriteAllText($p, $Text)
    Set-Mtime $p $MtimeIso
}

function New-GzFix {
    param([string]$Dir, [string]$Name, [string]$Text, [string]$MtimeIso)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
    $ms = [System.IO.MemoryStream]::new()
    $gz = [System.IO.Compression.GZipStream]::new($ms, [System.IO.Compression.CompressionMode]::Compress)
    $gz.Write($bytes, 0, $bytes.Length)
    $gz.Dispose()
    $p = Join-Path $Dir $Name
    [System.IO.File]::WriteAllBytes($p, $ms.ToArray())
    Set-Mtime $p $MtimeIso
}

function Read-GzText {
    param([string]$Path)
    $fs = [System.IO.File]::OpenRead($Path)
    $gz = [System.IO.Compression.GZipStream]::new($fs, [System.IO.Compression.CompressionMode]::Decompress)
    $sr = [System.IO.StreamReader]::new($gz, [System.Text.Encoding]::UTF8)
    $t = $sr.ReadToEnd()
    $sr.Dispose()
    $fs.Dispose()
    return $t
}

function Get-Tree {
    param([string]$Dir)
    $lines = @(foreach ($i in @(Get-ChildItem -LiteralPath $Dir -Force -File)) {
        '{0}|{1}|{2}' -f $i.Name, $i.LastWriteTimeUtc.Ticks, [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($i.FullName))
    })
    $arr = [string[]]$lines
    [Array]::Sort($arr, [System.StringComparer]::Ordinal)
    return ($arr -join "`n")
}

function Remove-WhatIfLines {
    param([string]$Text)
    $kept = @(($Text -split "`n") | Where-Object { -not $_.StartsWith('What if: ') })
    return ($kept -join "`n")
}

function New-RotDir {
    param([string]$Name)
    $d = Join-Path $T $Name
    [System.IO.Directory]::CreateDirectory($d) > $null
    New-LogFix $d 'app.2026-05-01.log' "may day one`n"        '2026-05-01T00:00:00Z'
    New-LogFix $d 'app.2026-06-10.log' "june ten`n"           '2026-06-10T00:00:00Z'
    New-LogFix $d 'app.2026-06-20.log' "june twenty`n"        '2026-06-20T00:00:00Z'
    New-LogFix $d 'app.2026-07-01.log' "july one`n"           '2026-07-01T00:00:00Z'
    New-LogFix $d 'app.2026-07-10.log' "july ten`n"           '2026-07-10T00:00:00Z'
    New-GzFix  $d 'app.2026-04-01.log.gz' "april`n"           '2026-04-01T00:00:00Z'
    New-GzFix  $d 'app.2026-06-25.log.gz' "june twenty-five`n" '2026-06-25T00:00:00Z'
    New-LogFix $d 'exactly.log' "boundary`n"                  '2026-06-16T00:00:00Z'
    New-LogFix $d 'readme.txt' "notes`n"                      '2026-06-01T00:00:00Z'
    return $d
}

$NOW = '2026-07-16T00:00:00Z'

$plan = @'
delete app.2026-04-01.log.gz
delete app.2026-05-01.log
delete app.2026-06-10.log
compress app.2026-06-20.log
keep app.2026-06-25.log.gz
keep app.2026-07-01.log
keep app.2026-07-10.log
compress exactly.log

'@

$tiePlan = @'
keep a.log
compress b.log

'@

# ShouldProcess is the contract, not an implementation choice.
$src = [System.IO.File]::ReadAllText($tool).ToLowerInvariant()
Assert-True 'source declares SupportsShouldProcess' ($src.Contains('supportsshouldprocess'))
Assert-True 'source gates mutations on ShouldProcess' ($src.Contains('.shouldprocess('))

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- -WhatIf: full plan, zero filesystem changes ---
    $dw = New-RotDir 'whatif'
    $before = Get-Tree $dw
    Invoke-Tool @('-Path', $dw, '-Now', $NOW, '-KeepCount', '2', '-WhatIf')
    Assert-True 'whatif: exit 0' ($RC -eq 0)
    Assert-Eq 'whatif: stderr empty' '' $ERR
    Assert-Eq 'whatif: plan' $plan (Remove-WhatIfLines $OUT)
    Assert-True 'whatif: ShouldProcess consulted for compress' ($OUT.Contains('What if:') -and $OUT.Contains('app.2026-06-20.log'))
    Assert-True 'whatif: ShouldProcess consulted for delete' ($OUT.Contains('app.2026-05-01.log'))
    Assert-Eq 'whatif: tree untouched' $before (Get-Tree $dw)

    # --- apply: same plan, mutations round-trip ---
    $da = New-RotDir 'apply'
    Invoke-Tool @('-Path', $da, '-Now', $NOW, '-KeepCount', '2')
    Assert-True 'apply: exit 0' ($RC -eq 0)
    Assert-Eq 'apply: stderr empty' '' $ERR
    Assert-Eq 'apply: plan' $plan $OUT
    $names = [string[]]@((Get-ChildItem -LiteralPath $da -Force -File).Name)
    [Array]::Sort($names, [System.StringComparer]::Ordinal)
    Assert-Eq 'apply: final file set' 'app.2026-06-20.log.gz app.2026-06-25.log.gz app.2026-07-01.log app.2026-07-10.log exactly.log.gz readme.txt' ($names -join ' ')
    Assert-Eq 'apply: compressed content round-trips' "june twenty`n" (Read-GzText (Join-Path $da 'app.2026-06-20.log.gz'))
    Assert-Eq 'apply: boundary file compressed' "boundary`n" (Read-GzText (Join-Path $da 'exactly.log.gz'))
    Assert-Eq 'apply: kept gz untouched' "june twenty-five`n" (Read-GzText (Join-Path $da 'app.2026-06-25.log.gz'))
    Assert-Eq 'apply: kept log untouched' "july one`n" ([System.IO.File]::ReadAllText((Join-Path $da 'app.2026-07-01.log')))
    Assert-Eq 'apply: non-log ignored' "notes`n" ([System.IO.File]::ReadAllText((Join-Path $da 'readme.txt')))

    # --- equal mtimes: the ordinal-earlier name is the one kept ---
    $dt = Join-Path $T 'tie'
    [System.IO.Directory]::CreateDirectory($dt) > $null
    New-LogFix $dt 'b.log' "bee`n" '2026-07-15T00:00:00Z'
    New-LogFix $dt 'a.log' "ay`n"  '2026-07-15T00:00:00Z'
    Invoke-Tool @('-Path', $dt, '-Now', $NOW, '-KeepCount', '1', '-WhatIf')
    Assert-True 'tie: exit 0' ($RC -eq 0)
    Assert-Eq 'tie: plan' $tiePlan (Remove-WhatIfLines $OUT)

    # --- nothing to rotate ---
    $de = Join-Path $T 'empty'
    [System.IO.Directory]::CreateDirectory($de) > $null
    Invoke-Tool @('-Path', $de, '-Now', $NOW)
    Assert-True 'empty: exit 0' ($RC -eq 0)
    Assert-Eq 'empty: no output' '' $OUT

    # --- -Now must parse ---
    Invoke-Tool @('-Path', $de, '-Now', 'not-a-time')
    Assert-True 'badnow: exit 64' ($RC -eq 64)
    Assert-Eq 'badnow: stdout empty' '' $OUT
    Assert-Eq 'badnow: message' "rotaplan: bad -Now value: not-a-time`n" $ERR

    # --- missing directory ---
    $gone = Join-Path $T 'gone'
    Invoke-Tool @('-Path', $gone, '-Now', $NOW)
    Assert-True 'missing: exit 66' ($RC -eq 66)
    Assert-Eq 'missing: stdout empty' '' $OUT
    Assert-Eq 'missing: message' "rotaplan: path not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
