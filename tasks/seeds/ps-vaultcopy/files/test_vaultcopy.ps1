# Regression harness for vaultcopy.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_vaultcopy.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'vaultcopy.ps1') -PathType Leaf)) {
    Write-Output 'FAIL vaultcopy.ps1 not found in the workspace root'
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
    param([string]$FromDir, [string[]]$CaseArgs = @())
    $outFile = Join-Path $T 'out'
    $errFile = Join-Path $T 'err'
    Push-Location -LiteralPath $FromDir
    try {
        & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'vaultcopy.ps1') @CaseArgs 1>$outFile 2>$errFile
        $script:RC = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    $script:OUT = [System.IO.File]::ReadAllText($outFile)
    $script:ERR = [System.IO.File]::ReadAllText($errFile)
}

function New-VaultSet {
    # Real names from the ops share: brackets and spaces included.
    $set = Join-Path $T 'vaultset'
    foreach ($d in @('configs', 'reports', 'logs')) {
        New-Item -ItemType Directory -Force -Path (Join-Path $set $d) > $null
    }
    [System.IO.File]::WriteAllText((Join-Path $set 'configs/site.yaml'), "retention: 30d`n")
    [System.IO.File]::WriteAllText((Join-Path $set 'reports/q3 review [draft 2].md'), "# q3 capacity review, second draft`n")
    [System.IO.File]::WriteAllText((Join-Path $set 'logs/job[2].log'), "literal job-two log`n")
    [System.IO.File]::WriteAllText((Join-Path $set 'logs/job2.log'), "rotated plain log`n")
}

$manifestFull = @'
# nightly vault set
_t/vaultset/configs/site.yaml
_t/vaultset/reports/q3 review [draft 2].md
_t/vaultset/logs/job[2].log
_t/vaultset/notes/decom checklist.txt
'@

$fullExpected = @'
copied _t/vaultset/configs/site.yaml
copied _t/vaultset/reports/q3 review [draft 2].md
copied _t/vaultset/logs/job[2].log
missing _t/vaultset/notes/decom checklist.txt
copied: 3
missing: 1

'@

$allExpected = @'
copied _t/vaultset/configs/site.yaml
copied _t/vaultset/reports/q3 review [draft 2].md
copied _t/vaultset/logs/job[2].log
copied: 3
missing: 0

'@

function Get-StagedText {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '(absent)' }
    return [System.IO.File]::ReadAllText($Path)
}

function Assert-Staged {
    param([string]$Label, [string]$DestName)
    $base = Join-Path (Join-Path $T $DestName) '_t/vaultset'
    Assert-Eq "${Label}: staged site.yaml" "retention: 30d`n" (Get-StagedText (Join-Path $base 'configs/site.yaml'))
    Assert-Eq "${Label}: staged draft report" "# q3 capacity review, second draft`n" (Get-StagedText (Join-Path $base 'reports/q3 review [draft 2].md'))
    Assert-Eq "${Label}: staged job[2].log carries the bracket file's bytes" "literal job-two log`n" (Get-StagedText (Join-Path $base 'logs/job[2].log'))
    Assert-True "${Label}: rotated job2.log was not staged (not in the manifest)" (-not (Test-Path -LiteralPath (Join-Path $base 'logs/job2.log')))
}

try {
    New-Item -ItemType Directory -Force -Path $T, (Join-Path $T 'elsewhere') > $null
    New-VaultSet
    $manifest = Join-Path $T 'vault.manifest'
    [System.IO.File]::WriteAllText($manifest, $manifestFull + "`n")

    # --- staging run started from the checkout root ---
    Invoke-Tool -FromDir $PSScriptRoot -CaseArgs @('-Manifest', $manifest, '-Dest', (Join-Path $T 'backupA'))
    Assert-True 'root run: exit 65 (one entry missing)' ($RC -eq 65)
    Assert-Eq 'root run: stderr empty' '' $ERR
    Assert-Eq 'root run: report' $fullExpected $OUT
    Assert-Staged 'root run' 'backupA'

    # --- the nightly job starts the tool from a different directory;
    # --- manifest entries still resolve against the checkout root ---
    Invoke-Tool -FromDir (Join-Path $T 'elsewhere') -CaseArgs @('-Manifest', $manifest, '-Dest', (Join-Path $T 'backupB'))
    Assert-True 'elsewhere run: exit 65' ($RC -eq 65)
    Assert-Eq 'elsewhere run: stderr empty' '' $ERR
    Assert-Eq 'elsewhere run: report' $fullExpected $OUT
    Assert-Staged 'elsewhere run' 'backupB'

    # --- a manifest whose every entry exists stages cleanly ---
    $manifest2 = Join-Path $T 'vault2.manifest'
    $manifestClean = @'
# nightly vault set
_t/vaultset/configs/site.yaml
_t/vaultset/reports/q3 review [draft 2].md
_t/vaultset/logs/job[2].log
'@
    [System.IO.File]::WriteAllText($manifest2, $manifestClean + "`n")
    Invoke-Tool -FromDir (Join-Path $T 'elsewhere') -CaseArgs @('-Manifest', $manifest2, '-Dest', (Join-Path $T 'backupC'))
    Assert-True 'clean run: exit 0' ($RC -eq 0)
    Assert-Eq 'clean run: stderr empty' '' $ERR
    Assert-Eq 'clean run: report' $allExpected $OUT
    Assert-Staged 'clean run' 'backupC'

    # --- missing manifest ---
    $gone = Join-Path $T 'gone.manifest'
    Invoke-Tool -FromDir $PSScriptRoot -CaseArgs @('-Manifest', $gone, '-Dest', (Join-Path $T 'backupD'))
    Assert-True 'missing manifest: exit 66' ($RC -eq 66)
    Assert-Eq 'missing manifest: stdout empty' '' $OUT
    Assert-Eq 'missing manifest: message' "vaultcopy: manifest not found: $gone`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
