Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$fixtureRoot = Join-Path -Path $projectRoot -ChildPath 'fixtures'
. (Join-Path -Path $projectRoot -ChildPath 'Repair-BackupGap.ps1')

$script:failures = 0

function Assert-Equal {
    param(
        [Parameter(Mandatory = $true)] $Expected,
        [Parameter(Mandatory = $true)] $Actual,
        [Parameter(Mandatory = $true)] [string] $Message
    )

    if ($Expected -cne $Actual) {
        throw "$Message`nExpected: <$Expected>`nActual:   <$Actual>"
    }
}

function Assert-True {
    param(
        [Parameter(Mandatory = $true)] [bool] $Condition,
        [Parameter(Mandatory = $true)] [string] $Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Invoke-Test {
    param(
        [Parameter(Mandatory = $true)] [string] $Name,
        [Parameter(Mandatory = $true)] [scriptblock] $Body
    )

    try {
        & $Body
        Write-Output "PASS $Name"
    }
    catch {
        $script:failures += 1
        Write-Output "FAIL $Name"
        Write-Output $_.Exception.Message
    }
}

function New-IncidentCase {
    $caseRoot = Join-Path -Path ([IO.Path]::GetTempPath()) -ChildPath ('powershell-backup-gap-' + [guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($caseRoot) | Out-Null
    Copy-Item -LiteralPath (Join-Path -Path $fixtureRoot -ChildPath 'source') -Destination (Join-Path -Path $caseRoot -ChildPath 'source') -Recurse
    Copy-Item -LiteralPath (Join-Path -Path $fixtureRoot -ChildPath 'partial-backup') -Destination (Join-Path -Path $caseRoot -ChildPath 'backup') -Recurse
    Copy-Item -LiteralPath (Join-Path -Path $fixtureRoot -ChildPath 'incident-manifest.json') -Destination (Join-Path -Path $caseRoot -ChildPath 'manifest.json')
    Copy-Item -LiteralPath (Join-Path -Path $fixtureRoot -ChildPath 'incident-transcript.log') -Destination (Join-Path -Path $caseRoot -ChildPath 'transcript.log')
    return $caseRoot
}

function Invoke-CaseRepair {
    param([Parameter(Mandatory = $true)] [string] $CaseRoot)

    return Repair-BackupGap `
        -SourceRoot (Join-Path -Path $CaseRoot -ChildPath 'source') `
        -BackupRoot (Join-Path -Path $CaseRoot -ChildPath 'backup') `
        -ManifestPath (Join-Path -Path $CaseRoot -ChildPath 'manifest.json') `
        -TranscriptPath (Join-Path -Path $CaseRoot -ChildPath 'transcript.log') `
        -AuditPath (Join-Path -Path $CaseRoot -ChildPath 'audit.txt')
}

Invoke-Test 'preserves the recorded native exit code' {
    $evidence = Get-BackupTranscriptEvidence -TranscriptPath (Join-Path -Path $fixtureRoot -ChildPath 'incident-transcript.log')
    Assert-Equal 23 $evidence.NativeExitCode 'The native partial-failure exit code was masked.'
    Assert-Equal 'docs/quarterly report.txt,keys/public.txt' ($evidence.SkippedPaths -join ',') 'Skipped paths were not diagnosed exactly.'
}

Invoke-Test 'resumes skipped files and emits the exact audit report' {
    $caseRoot = New-IncidentCase
    try {
        $catalogPath = Join-Path -Path $caseRoot -ChildPath 'backup/db/catalog.txt'
        $preservedTimestamp = [datetime]::SpecifyKind([datetime]'2020-01-02T03:04:05', [DateTimeKind]::Utc)
        [IO.File]::SetLastWriteTimeUtc($catalogPath, $preservedTimestamp)

        $result = Invoke-CaseRepair -CaseRoot $caseRoot

        Assert-Equal 'partial_failure' $result.IncidentStatus 'Original incident status was not retained.'
        Assert-Equal 23 $result.NativeExitCode 'Native exit code was not retained.'
        Assert-Equal 'recovered' $result.Result 'Recovery result was not reported separately.'
        Assert-Equal 'docs/quarterly report.txt,keys/public.txt' ($result.ResumedFiles -join ',') 'Wrong resume set.'
        Assert-Equal $preservedTimestamp ([IO.File]::GetLastWriteTimeUtc($catalogPath)) 'A verified destination file was overwritten.'

        $expectedAudit = (@(
                'run_id=backup-20260718T031500Z',
                'incident_status=partial_failure',
                'native_exit_code=23',
                'evidence=manifest+transcript',
                'preserved_count=2',
                'resumed_count=2',
                'verified_count=4',
                'preserved=db/catalog.txt,media/logo.txt',
                'resumed=docs/quarterly report.txt,keys/public.txt',
                'result=recovered'
            ) -join "`n") + "`n"
        $auditPath = Join-Path -Path $caseRoot -ChildPath 'audit.txt'
        Assert-Equal $expectedAudit ([IO.File]::ReadAllText($auditPath)) 'Audit report bytes differ from the contract.'
        $auditBytes = [IO.File]::ReadAllBytes($auditPath)
        Assert-True (-not ($auditBytes.Length -ge 3 -and $auditBytes[0] -eq 0xEF -and $auditBytes[1] -eq 0xBB -and $auditBytes[2] -eq 0xBF)) 'Audit report has a UTF-8 BOM.'

        foreach ($relativePath in @('db/catalog.txt', 'docs/quarterly report.txt', 'keys/public.txt', 'media/logo.txt')) {
            $sourceHash = Get-BackupFileHash -LiteralPath (Join-Path -Path (Join-Path -Path $caseRoot -ChildPath 'source') -ChildPath $relativePath)
            $backupHash = Get-BackupFileHash -LiteralPath (Join-Path -Path (Join-Path -Path $caseRoot -ChildPath 'backup') -ChildPath $relativePath)
            Assert-Equal $sourceHash $backupHash "Recovered hash differs for $relativePath."
        }
    }
    finally {
        if (Test-Path -LiteralPath $caseRoot) {
            Remove-Item -LiteralPath $caseRoot -Recurse -Force
        }
    }
}

Invoke-Test 'refuses a conflicting destination before copying anything' {
    $caseRoot = New-IncidentCase
    try {
        $catalogPath = Join-Path -Path $caseRoot -ChildPath 'backup/db/catalog.txt'
        [IO.File]::WriteAllText($catalogPath, "tampered`n", [Text.UTF8Encoding]::new($false))
        $caught = $null

        try {
            Invoke-CaseRepair -CaseRoot $caseRoot | Out-Null
        }
        catch {
            $caught = $_
        }

        Assert-True ($null -ne $caught) 'Conflicting destination was accepted.'
        Assert-True ($caught.Exception.Message -like 'Refusing to overwrite conflicting backup file:*') 'Conflict error was not specific.'
        Assert-Equal "tampered`n" ([IO.File]::ReadAllText($catalogPath)) 'Conflicting destination was changed.'
        Assert-True (-not (Test-Path -LiteralPath (Join-Path -Path $caseRoot -ChildPath 'backup/docs/quarterly report.txt'))) 'Preflight failure still copied a skipped file.'
        Assert-True (-not (Test-Path -LiteralPath (Join-Path -Path $caseRoot -ChildPath 'audit.txt'))) 'Preflight failure emitted an audit report.'
    }
    finally {
        if (Test-Path -LiteralPath $caseRoot) {
            Remove-Item -LiteralPath $caseRoot -Recurse -Force
        }
    }
}

if ($script:failures -ne 0) {
    Write-Output "$script:failures test(s) failed."
    exit 1
}

Write-Output 'All tests passed.'
