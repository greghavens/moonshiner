# Regression harness for enrolldesk.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_enrolldesk.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'enrolldesk.ps1') -PathType Leaf)) {
    Write-Output 'FAIL enrolldesk.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'enrolldesk.ps1') @CaseArgs 1>$outFile 2>$errFile
    $script:RC = $LASTEXITCODE
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function Write-Fixture {
    param([string]$Name, [string]$Content)
    $p = Join-Path $T $Name
    [System.IO.File]::WriteAllText($p, $Content)
    return $p
}

$rosterCsv = @'
name,team
Pia Almgren,lifeguards
Dee O'Brien,boat crew
Sam Tally,front desk
Nils Voss,night watch
Ivy Dean,gate
'@

$codesTxt = @'
Pia Almgren=Otter7
Dee O'Brien=Coble4
Sam Tally=Gate12
Nils Voss=Harbor$Watch
Ivy Dean=Dock=Side2
'@

$dayExpected = @'
card|Pia Almgren|lifeguards|len=6|code=Otter7
card|Dee O'Brien|boat crew|len=6|code=Coble4
card|Sam Tally|front desk|len=6|code=Gate12
card|Nils Voss|night watch|len=12|code=Harbor$Watch
card|Ivy Dean|gate|len=10|code=Dock=Side2
card|relief-desk|floaters|len=6|code=Kiosk9
enrolled 5 members, 1 relief card

'@

$strayRoster = @'
name,team
Pia Almgren,lifeguards
Kit Marlowe,gate
'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    # --- full signup day: names with apostrophes and spaces, codes with $ and = ---
    $roster = Write-Fixture 'roster.csv' $rosterCsv
    $codes = Write-Fixture 'codes.txt' $codesTxt
    Invoke-Tool @('-Roster', $roster, '-Codes', $codes)
    Assert-True 'day: exit 0' ($RC -eq 0)
    Assert-Eq 'day: stderr empty' '' $ERR
    Assert-Eq 'day: cards' $dayExpected $OUT

    # --- member with no code on file: refuse before issuing anything ---
    $stray = Write-Fixture 'stray.csv' $strayRoster
    Invoke-Tool @('-Roster', $stray, '-Codes', $codes)
    Assert-True 'stray: exit 65' ($RC -eq 65)
    Assert-Eq 'stray: stdout empty' '' $OUT
    Assert-Eq 'stray: message' "enrolldesk: no code on file for Kit Marlowe`n" $ERR

    # --- missing roster ---
    $gone = Join-Path $T 'gone.csv'
    Invoke-Tool @('-Roster', $gone, '-Codes', $codes)
    Assert-True 'noroster: exit 66' ($RC -eq 66)
    Assert-Eq 'noroster: stdout empty' '' $OUT
    Assert-Eq 'noroster: message' "enrolldesk: roster not found: $gone`n" $ERR

    # --- missing codes file ---
    $gonec = Join-Path $T 'gone.txt'
    Invoke-Tool @('-Roster', $roster, '-Codes', $gonec)
    Assert-True 'nocodes: exit 66' ($RC -eq 66)
    Assert-Eq 'nocodes: stdout empty' '' $OUT
    Assert-Eq 'nocodes: message' "enrolldesk: codes file not found: $gonec`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
