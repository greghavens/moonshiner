# Acceptance harness for pipetally.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_pipetally.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$lib = Join-Path $PSScriptRoot 'pipetally.ps1'
if (-not (Test-Path -LiteralPath $lib -PathType Leaf)) {
    Write-Output 'FAIL pipetally.ps1 not found in the workspace root'
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
# under strict mode and prints a labeled line per call; the block is compared
# byte-exact.
$driver = @'
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'pipetally.ps1')

function Out-Line { param([string]$Label, [string]$Value) Write-Output "$Label=[$Value]" }
function Out-Err {
    param([string]$Label, [scriptblock]$Body)
    try { $null = & $Body; Write-Output "$Label=[NO-ERROR]" }
    catch { Write-Output "$Label=[ERR $($_.Exception.Message)]" }
}

$scans = @(
    [pscustomobject]@{ Id = 'K-201'; Dock = 'north' },
    [pscustomobject]@{ Id = 'K-105'; Dock = 'south' },
    [pscustomobject]@{ Id = 'K-201'; Dock = 'east' },
    [pscustomobject]@{ Id = 'K-330'; Dock = 'north' },
    [pscustomobject]@{ Id = 'K-105'; Dock = 'west' }
)

$u = @($scans | Select-UniqueRecord)
Out-Line 'pipe.ids' (($u | ForEach-Object Id) -join ',')
Out-Line 'pipe.firstdock' ($u[0].Dock)
Out-Line 'pipe.count' ($u.Count)

$v = @(Select-UniqueRecord -InputObject $scans)
Out-Line 'arg.ids' (($v | ForEach-Object Id) -join ',')
Out-Line 'equiv.records' ((ConvertTo-Json $u -Depth 4) -ceq (ConvertTo-Json $v -Depth 4))

$cased = @(
    [pscustomobject]@{ Id = 'srv-A' },
    [pscustomobject]@{ Id = 'srv-a' },
    [pscustomobject]@{ Id = 'srv-A' }
)
$cu = @($cased | Select-UniqueRecord)
Out-Line 'case.count' ($cu.Count)
Out-Line 'case.ids' (($cu | ForEach-Object Id) -join ',')

$mixed = @(
    [pscustomobject]@{ Id = 7 },
    [pscustomobject]@{ Id = '7' }
)
Out-Line 'mixed.count' (@($mixed | Select-UniqueRecord).Count)

$holey = @($null, [pscustomobject]@{ Id = 'K-9' }, $null)
Out-Line 'nulls.pipe' ((@($holey | Select-UniqueRecord) | ForEach-Object Id) -join ',')
Out-Line 'nulls.arg' ((@(Select-UniqueRecord -InputObject $holey) | ForEach-Object Id) -join ',')

$byOwner = @(
    [pscustomobject]@{ Id = 'K-1'; Owner = 'dockmgr' },
    [pscustomobject]@{ Id = 'K-2'; Owner = 'fieldtech' },
    [pscustomobject]@{ Id = 'K-3'; Owner = 'dockmgr' }
)
Out-Line 'key.owner' ((@($byOwner | Select-UniqueRecord -Key Owner) | ForEach-Object Owner) -join ',')

Out-Err 'key.err' { @([pscustomobject]@{ Name = 'stray' }) | Select-UniqueRecord }

$s = @($scans | Select-UniqueRecord -Summary)
Out-Line 'sum.pairs' (($s | ForEach-Object { '{0}:{1}' -f $_.Key, $_.Count }) -join ' ')
Out-Line 'sum.props' ($s[0].PSObject.Properties.Name -join ',')
Out-Line 'sum.type' ($s[0] -is [System.Management.Automation.PSCustomObject])
Out-Line 'sum.counttype' ($s[0].Count.GetType().Name)
Out-Line 'equiv.summary' ((ConvertTo-Json $s -Depth 4) -ceq (ConvertTo-Json @(Select-UniqueRecord -InputObject $scans -Summary) -Depth 4))

Out-Line 'empty.pipe' (@(@() | Select-UniqueRecord).Count)
Out-Line 'empty.arg' (@(Select-UniqueRecord -InputObject @()).Count)
Out-Line 'empty.sum' (@(Select-UniqueRecord -InputObject @() -Summary).Count)

$log = [System.Collections.Generic.List[string]]::new()
@('a', 'b', 'a', 'c') | ForEach-Object { $log.Add("feed:$_"); [pscustomobject]@{ Id = $_ } } |
    Select-UniqueRecord | ForEach-Object { $log.Add("emit:$($_.Id)") }
Out-Line 'stream' ($log -join ' ')
'@

$expected = @'
pipe.ids=[K-201,K-105,K-330]
pipe.firstdock=[north]
pipe.count=[3]
arg.ids=[K-201,K-105,K-330]
equiv.records=[True]
case.count=[2]
case.ids=[srv-A,srv-a]
mixed.count=[1]
nulls.pipe=[K-9]
nulls.arg=[K-9]
key.owner=[dockmgr,fieldtech]
key.err=[ERR pipetally: record is missing key property 'Id']
sum.pairs=[K-201:2 K-105:2 K-330:1]
sum.props=[Key,Count]
sum.type=[True]
sum.counttype=[Int32]
equiv.summary=[True]
empty.pipe=[0]
empty.arg=[0]
empty.sum=[0]
stream=[feed:a emit:a feed:b emit:b feed:a feed:c emit:c]

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
