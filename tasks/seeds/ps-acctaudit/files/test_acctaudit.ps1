# Acceptance harness for acctaudit.ps1.
# Run from the workspace root:  pwsh -NoProfile -File test_acctaudit.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$PSStyle.OutputRendering = 'PlainText'

Set-Location -LiteralPath $PSScriptRoot

$tool = Join-Path $PSScriptRoot 'acctaudit.ps1'
if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
    Write-Output 'FAIL acctaudit.ps1 not found in the workspace root'
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
    & pwsh -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot 'acctaudit.ps1') @CaseArgs 1>$outFile 2>$errFile
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

$dirtyReport = @'
{
  "orphanedMembers": [
    {
      "group": "users",
      "member": "ghost"
    },
    {
      "group": "wheel",
      "member": "Alice"
    }
  ],
  "duplicateUids": [
    {
      "uid": 995,
      "users": [
        "eve",
        "frank"
      ]
    },
    {
      "uid": 1002,
      "users": [
        "bob",
        "carol"
      ]
    }
  ],
  "duplicateGids": [
    {
      "gid": 50,
      "groups": [
        "backup",
        "legacy"
      ]
    }
  ],
  "shellViolations": [
    {
      "user": "svc-backup",
      "shell": "/bin/csh"
    }
  ],
  "unknownPrimaryGroup": [
    {
      "user": "dana",
      "gid": 2000
    },
    {
      "user": "svc-backup",
      "gid": 990
    }
  ]
}

'@

$cleanReport = @'
{
  "orphanedMembers": [],
  "duplicateUids": [],
  "duplicateGids": [],
  "shellViolations": [],
  "unknownPrimaryGroup": []
}

'@

try {
    New-Item -ItemType Directory -Force -Path $T > $null

    $passwd = Write-Fixture 'passwd.db' @'
root:x:0:0:root:/root:/bin/bash
alice:x:1001:100:Alice Doe:/home/alice:/bin/bash
bob:x:1002:100:Bob Ray:/home/bob:/bin/zsh
carol:x:1002:100:Carol:/home/carol:/bin/bash
svc-backup:x:990:990:backup service:/var/backup:/bin/csh
dana:x:1004:2000:Dana:/home/dana:/usr/sbin/nologin
eve:x:995:100:Eve:/home/eve:/bin/bash
frank:x:995:100:Frank:/home/frank:/bin/zsh
'@

    $group = Write-Fixture 'group.db' @'
root:x:0:
users:x:100:alice,bob,carol,ghost
wheel:x:10:Alice,root
backup:x:50:svc-backup
legacy:x:50:
'@

    $shells = Write-Fixture 'shells.allow' @'
# login shells cleared by the security review

/bin/bash
/bin/zsh
/usr/sbin/nologin
'@

    # --- the dirty snapshot: every finding class at once ---
    Invoke-Tool @('-PasswdPath', $passwd, '-GroupPath', $group, '-AllowedShells', $shells)
    Assert-True 'dirty: exit 65' ($RC -eq 65)
    Assert-Eq 'dirty: stderr empty' '' $ERR
    Assert-Eq 'dirty: report' $dirtyReport $OUT

    # --- a clean snapshot reports empty arrays and exits 0 ---
    $passwd2 = Write-Fixture 'passwd2.db' @'
root:x:0:0:root:/root:/bin/bash
amy:x:1:1:Amy:/home/amy:/bin/zsh
'@
    $group2 = Write-Fixture 'group2.db' @'
root:x:0:
staff:x:1:amy,root
'@
    Invoke-Tool @('-PasswdPath', $passwd2, '-GroupPath', $group2, '-AllowedShells', $shells)
    Assert-True 'clean: exit 0' ($RC -eq 0)
    Assert-Eq 'clean: stderr empty' '' $ERR
    Assert-Eq 'clean: report' $cleanReport $OUT

    # --- a passwd row with the wrong field count stops the audit ---
    $badp = Write-Fixture 'badp.db' @'
root:x:0:0:root:/root:/bin/bash
oops:x:77:77:/home/oops:/bin/bash
'@
    Invoke-Tool @('-PasswdPath', $badp, '-GroupPath', $group2, '-AllowedShells', $shells)
    Assert-True 'badpasswd: exit 64' ($RC -eq 64)
    Assert-Eq 'badpasswd: stdout empty' '' $OUT
    Assert-Eq 'badpasswd: message' "acctaudit: badp.db: malformed line 2`n" $ERR

    # --- a group row with a non-numeric gid stops the audit ---
    $badg = Write-Fixture 'badg.db' @'
root:x:zero:
'@
    Invoke-Tool @('-PasswdPath', $passwd2, '-GroupPath', $badg, '-AllowedShells', $shells)
    Assert-True 'badgroup: exit 64' ($RC -eq 64)
    Assert-Eq 'badgroup: stdout empty' '' $OUT
    Assert-Eq 'badgroup: message' "acctaudit: badg.db: malformed line 1`n" $ERR
} finally {
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fails -gt 0) {
    Write-Output "$fails of $checks checks failed"
    exit 1
}
Write-Output "all checks passed ($checks checks)"
exit 0
