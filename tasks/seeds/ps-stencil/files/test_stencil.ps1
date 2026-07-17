# Acceptance harness for stencil.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_stencil.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$lib = Join-Path $PSScriptRoot 'stencil.ps1'
if (-not (Test-Path -LiteralPath $lib -PathType Leaf)) {
    Write-Output 'FAIL stencil.ps1 not found in the workspace root'
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

# The engine must be a manual scan, not expression evaluation; the source is
# part of the contract.
$src = [System.IO.File]::ReadAllText($lib)
$srcLower = $src.ToLowerInvariant()
foreach ($banned in @('invoke-expression', '$executioncontext', 'expandstring')) {
    Assert-True "source does not use $banned" (-not $srcLower.Contains($banned))
}
Assert-True 'source does not use the iex alias' (-not [regex]::IsMatch($src, '(?i)(?<![\w-])iex(?![\w-])'))

# A real multiline template, shipped to the driver as a file, authored here
# as a literal here-string exactly the way the deploy scripts hold them.
$noticeTemplate = @'
Hello {{name}},

maintenance window {{window}} on {{host}} is confirmed.
Escaped literal: {{{{not-a-key}} stays.
-- {{team}}
'@

$driver = @'
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'stencil.ps1')

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
function Fmt-Keys {
    param([string]$Template)
    $ks = @(Get-StencilKeys -Template $Template)
    if ($ks.Count -eq 0) { return 'NONE' }
    return $ks -join '|'
}

$tpl = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'notice.tpl') -Raw
Out-Line 'tplfile' (Expand-Stencil -Template $tpl -Values @{ name = 'Dana'; window = '03:00-03:30'; host = 'cache01'; team = 'Night Ops' })

Out-Line 'repeat' (Expand-Stencil -Template 'host={{host}} backup={{host}}' -Values @{ host = 'cache01' })
Out-Line 'int' (Expand-Stencil -Template 'run {{n}} of {{total}}' -Values @{ n = 3; total = 12 })
Out-Line 'nullval' (Expand-Stencil -Template '<{{gone}}>' -Values @{ gone = $null })
Out-Line 'esc.basic' (Expand-Stencil -Template 'literal {{{{ brace' -Values @{})
Out-Line 'esc.then.token' (Expand-Stencil -Template '{{{{name}} and {{name}}' -Values @{ name = 'x' })
Out-Line 'lone' (Expand-Stencil -Template 'a } b { c }} d' -Values @{})
Out-Line 'dotted' (Expand-Stencil -Template '{{svc.name}} {{env_tier}}' -Values @{ 'svc.name' = 'cache'; 'env_tier' = 'prod' })
Out-Line 'empty.tpl' (Expand-Stencil -Template '' -Values @{})

Out-Err 'missing.default' { Expand-Stencil -Template '{{name}} {{Name}}' -Values @{ name = 'x' } }
Out-Err 'missing.error' { Expand-Stencil -Template '{{name}} {{Name}}' -Values @{ name = 'x' } -OnMissing Error }
Out-Line 'missing.empty' (Expand-Stencil -Template '{{name}} {{Name}}' -Values @{ name = 'x' } -OnMissing Empty)
Out-Line 'missing.keep' (Expand-Stencil -Template '{{name}} {{Name}}' -Values @{ name = 'x' } -OnMissing Keep)
Out-Line 'missing.keep.case' (Expand-Stencil -Template '{{NAME}}' -Values @{ name = 'x' } -OnMissing Keep)
Out-Line 'singlepass' (Expand-Stencil -Template '{{a}}' -Values @{ a = '{{b}}'; b = 'x' })

Out-Err 'unclosed' { Expand-Stencil -Template 'start {{name and more' -Values @{ name = 'x' } }
Out-Err 'invalidkey' { Expand-Stencil -Template '{{bad key}}' -Values @{} }
Out-Threw 'emptykey' { Expand-Stencil -Template '{{}}' -Values @{} }
Out-Threw 'badpolicy' { Expand-Stencil -Template '{{a}}' -Values @{ a = 1 } -OnMissing Silent }

Out-Line 'keys.multi' (Fmt-Keys '{{b}} {{a}} {{B}} {{_x}} {{a}}')
Out-Line 'keys.none' (Fmt-Keys 'plain text')
Out-Line 'keys.esc' (Fmt-Keys '{{{{skip}} {{real}}')
'@

$expected = @'
tplfile=[Hello Dana,

maintenance window 03:00-03:30 on cache01 is confirmed.
Escaped literal: {{not-a-key}} stays.
-- Night Ops]
repeat=[host=cache01 backup=cache01]
int=[run 3 of 12]
nullval=[<>]
esc.basic=[literal {{ brace]
esc.then.token=[{{name}} and x]
lone=[a } b { c }} d]
dotted=[cache prod]
empty.tpl=[]
missing.default=[ERR stencil: missing key: Name]
missing.error=[ERR stencil: missing key: Name]
missing.empty=[x ]
missing.keep=[x {{Name}}]
missing.keep.case=[{{NAME}}]
singlepass=[{{b}}]
unclosed=[ERR stencil: unclosed token at offset 6]
invalidkey=[ERR stencil: invalid key: bad key]
emptykey=[THREW]
badpolicy=[THREW]
keys.multi=[B|_x|a|b]
keys.none=[NONE]
keys.esc=[real]

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null
    [System.IO.File]::WriteAllText((Join-Path $T 'notice.tpl'), $noticeTemplate)
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
