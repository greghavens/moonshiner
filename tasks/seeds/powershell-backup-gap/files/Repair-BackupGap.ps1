Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-BackupFileHash {
    param(
        [Parameter(Mandatory = $true)]
        [string] $LiteralPath
    )

    return (Get-FileHash -LiteralPath $LiteralPath -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Resolve-BackupEntryPath {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Root,

        [Parameter(Mandatory = $true)]
        [string] $RelativePath
    )

    if ([string]::IsNullOrWhiteSpace($RelativePath) -or
        [IO.Path]::IsPathRooted($RelativePath) -or
        $RelativePath -match '(^|[\\/])\.\.([\\/]|$)') {
        throw "Unsafe manifest path: $RelativePath"
    }

    $separator = [IO.Path]::DirectorySeparatorChar
    $nativeRelativePath = $RelativePath.Replace('/', $separator)
    $fullRoot = [IO.Path]::GetFullPath($Root)
    $fullPath = [IO.Path]::GetFullPath((Join-Path -Path $fullRoot -ChildPath $nativeRelativePath))
    $rootPrefix = $fullRoot.TrimEnd([char[]]@(
            [IO.Path]::DirectorySeparatorChar,
            [IO.Path]::AltDirectorySeparatorChar
        )) + $separator
    $comparison = if ($env:OS -eq 'Windows_NT') {
        [StringComparison]::OrdinalIgnoreCase
    }
    else {
        [StringComparison]::Ordinal
    }

    if (-not $fullPath.StartsWith($rootPrefix, $comparison)) {
        throw "Manifest path escapes its root: $RelativePath"
    }

    return $fullPath
}

function Get-BackupTranscriptEvidence {
    param(
        [Parameter(Mandatory = $true)]
        [string] $TranscriptPath
    )

    $exitCodeMatches = @(Select-String -LiteralPath $TranscriptPath -Pattern '^NATIVE_EXIT_CODE=([0-9]+)$')
    if ($exitCodeMatches.Count -ne 1) {
        throw 'Transcript must contain exactly one NATIVE_EXIT_CODE record.'
    }

    $recordedExitCode = [int] $exitCodeMatches[0].Matches[0].Groups[1].Value
    $skippedMatches = @(Select-String -LiteralPath $TranscriptPath -Pattern '^SKIPPED\|([^|]+)\|[^|]+$')
    $skippedPaths = @($skippedMatches | ForEach-Object {
            $_.Matches[0].Groups[1].Value
        } | Sort-Object)

    # BUG: $? now describes Select-String, not the native backup recorded by the transcript.
    if ($?) {
        $recordedExitCode = 0
    }

    return [pscustomobject]@{
        NativeExitCode = $recordedExitCode
        SkippedPaths   = $skippedPaths
    }
}

function Repair-BackupGap {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $SourceRoot,

        [Parameter(Mandatory = $true)]
        [string] $BackupRoot,

        [Parameter(Mandatory = $true)]
        [string] $ManifestPath,

        [Parameter(Mandatory = $true)]
        [string] $TranscriptPath,

        [Parameter(Mandatory = $true)]
        [string] $AuditPath
    )

    $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    $evidence = Get-BackupTranscriptEvidence -TranscriptPath $TranscriptPath

    if ($manifest.status -ne 'partial_failure') {
        throw "Manifest status must be partial_failure, got $($manifest.status)."
    }

    if ([int] $manifest.native_exit_code -ne $evidence.NativeExitCode) {
        throw "Evidence disagreement: manifest exit code $($manifest.native_exit_code), transcript exit code $($evidence.NativeExitCode)."
    }

    if ($evidence.NativeExitCode -eq 0) {
        throw 'A zero native exit code does not describe a partial failure.'
    }

    $entries = @($manifest.files | Sort-Object -Property path)
    if ($entries.Count -eq 0) {
        throw 'Manifest contains no files.'
    }

    $manifestSkipped = @($entries | Where-Object { $_.state -eq 'skipped' } | ForEach-Object {
            [string] $_.path
        } | Sort-Object)
    $evidenceDifference = @(Compare-Object -ReferenceObject $manifestSkipped -DifferenceObject $evidence.SkippedPaths)
    if ($evidenceDifference.Count -ne 0) {
        throw 'Evidence disagreement: transcript and manifest skipped paths differ.'
    }

    $preserved = [Collections.Generic.List[string]]::new()
    $resumePlan = [Collections.Generic.List[object]]::new()

    # Validate every source and destination before changing the backup.
    foreach ($entry in $entries) {
        if ($entry.state -notin @('copied', 'skipped')) {
            throw "Unsupported manifest state for $($entry.path): $($entry.state)"
        }

        $sourcePath = Resolve-BackupEntryPath -Root $SourceRoot -RelativePath $entry.path
        $destinationPath = Resolve-BackupEntryPath -Root $BackupRoot -RelativePath $entry.path

        if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
            throw "Source file is missing: $($entry.path)"
        }

        $sourceHash = Get-BackupFileHash -LiteralPath $sourcePath
        if ($sourceHash -ne ([string] $entry.sha256).ToLowerInvariant()) {
            throw "Source hash drifted: $($entry.path)"
        }

        if (Test-Path -LiteralPath $destinationPath -PathType Leaf) {
            $destinationHash = Get-BackupFileHash -LiteralPath $destinationPath
            if ($destinationHash -ne $sourceHash) {
                throw "Refusing to overwrite conflicting backup file: $($entry.path)"
            }

            $preserved.Add([string] $entry.path)
            continue
        }

        if ($entry.state -ne 'skipped') {
            throw "Manifest says copied but backup file is missing: $($entry.path)"
        }

        $resumePlan.Add([pscustomobject]@{
                Path            = [string] $entry.path
                Source          = $sourcePath
                Destination     = $destinationPath
                ExpectedSha256  = $sourceHash
            })
    }

    $resumed = [Collections.Generic.List[string]]::new()
    foreach ($item in $resumePlan) {
        $destinationDirectory = Split-Path -Parent $item.Destination
        [IO.Directory]::CreateDirectory($destinationDirectory) | Out-Null
        $temporaryPath = Join-Path -Path $destinationDirectory -ChildPath ('.' + [IO.Path]::GetFileName($item.Destination) + '.resume-' + $manifest.run_id + '.tmp')

        if (Test-Path -LiteralPath $temporaryPath) {
            throw "Refusing to replace stale resume file: $temporaryPath"
        }

        try {
            [IO.File]::Copy($item.Source, $temporaryPath, $false)
            if ((Get-BackupFileHash -LiteralPath $temporaryPath) -ne $item.ExpectedSha256) {
                throw "Resume copy failed verification: $($item.Path)"
            }
            if (Test-Path -LiteralPath $item.Destination) {
                throw "Destination appeared during resume: $($item.Path)"
            }
            [IO.File]::Move($temporaryPath, $item.Destination)
        }
        finally {
            if (Test-Path -LiteralPath $temporaryPath) {
                Remove-Item -LiteralPath $temporaryPath -Force
            }
        }

        $resumed.Add($item.Path)
    }

    $verified = 0
    foreach ($entry in $entries) {
        $destinationPath = Resolve-BackupEntryPath -Root $BackupRoot -RelativePath $entry.path
        if (-not (Test-Path -LiteralPath $destinationPath -PathType Leaf) -or
            (Get-BackupFileHash -LiteralPath $destinationPath) -ne ([string] $entry.sha256).ToLowerInvariant()) {
            throw "Post-resume verification failed: $($entry.path)"
        }
        $verified += 1
    }

    $auditLines = @(
        "run_id=$($manifest.run_id)",
        "incident_status=$($manifest.status)",
        "native_exit_code=$($evidence.NativeExitCode)",
        'evidence=manifest+transcript',
        "preserved_count=$($preserved.Count)",
        "resumed_count=$($resumed.Count)",
        "verified_count=$verified",
        "preserved=$($preserved -join ',')",
        "resumed=$($resumed -join ',')",
        'result=recovered'
    )
    $audit = ($auditLines -join "`n") + "`n"
    [IO.File]::WriteAllText($AuditPath, $audit, [Text.UTF8Encoding]::new($false))

    return [pscustomobject]@{
        RunId           = [string] $manifest.run_id
        IncidentStatus  = [string] $manifest.status
        NativeExitCode  = $evidence.NativeExitCode
        Result           = 'recovered'
        PreservedFiles   = @($preserved)
        ResumedFiles     = @($resumed)
        VerifiedCount    = $verified
    }
}
