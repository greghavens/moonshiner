[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputDirectory,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [string]$CheckpointPath,

    [ValidateRange(0, 2147483647)]
    [int]$BatchSize = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$InvariantCulture = [Globalization.CultureInfo]::InvariantCulture
$Utf8NoBom = [Text.UTF8Encoding]::new($false)
$RequiredColumns = @(
    'directory_id',
    'parent_directory_id',
    'relative_path',
    'owner_upn',
    'size_mib',
    'modified_local',
    'utc_offset_minutes',
    'is_deleted'
)

function Write-Utf8Text {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text
    )

    [IO.File]::WriteAllText($Path, $Text, $script:Utf8NoBom)
}

function Add-Utf8Line {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Line
    )

    [IO.File]::AppendAllText($Path, $Line + "`n", $script:Utf8NoBom)
}

function ConvertTo-StableCsvLine {
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Values)

    $encoded = foreach ($value in $Values) {
        $text = if ($null -eq $value) { '' } else { [string]$value }
        '"' + $text.Replace('"', '""') + '"'
    }
    return $encoded -join ','
}

function Get-TextSha256 {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)

    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $script:Utf8NoBom.GetBytes($Text)
        $hash = $algorithm.ComputeHash($bytes)
        return ([BitConverter]::ToString($hash)).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
    }
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    $algorithm = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try {
        $hash = $algorithm.ComputeHash($stream)
        return ([BitConverter]::ToString($hash)).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $stream.Dispose()
        $algorithm.Dispose()
    }
}

function Get-StableRecordId {
    param([Parameter(Mandatory = $true)][string]$DirectoryId)

    $canonicalId = $DirectoryId.Trim().ToLowerInvariant()
    $digest = Get-TextSha256 -Text ("directory-v2`n" + $canonicalId)
    return 'dir_' + $digest.Substring(0, 32)
}

function Write-Checkpoint {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$State
    )

    $json = $State | ConvertTo-Json -Depth 4 -Compress
    $temporaryPath = $Path + '.tmp'
    Write-Utf8Text -Path $temporaryPath -Text ($json + "`n")
    [IO.File]::Move($temporaryPath, $Path, $true)
}

function Get-InputFingerprint {
    param([Parameter(Mandatory = $true)][string[]]$Files)

    $parts = foreach ($file in $Files) {
        [IO.Path]::GetFileName($file) + "`0" + (Get-FileSha256 -Path $file)
    }
    return Get-TextSha256 -Text ($parts -join "`n")
}

function Get-StableAuditJson {
    param([Parameter(Mandatory = $true)][Collections.IDictionary]$Event)

    return $Event | ConvertTo-Json -Compress
}

$inputPath = [IO.Path]::GetFullPath($InputDirectory)
$outputPath = [IO.Path]::GetFullPath($OutputDirectory)

if (-not [IO.Directory]::Exists($inputPath)) {
    throw "Input directory does not exist: $inputPath"
}
if ($inputPath -eq $outputPath) {
    throw 'InputDirectory and OutputDirectory must be different directories.'
}

[IO.Directory]::CreateDirectory($outputPath) | Out-Null
if ([string]::IsNullOrWhiteSpace($CheckpointPath)) {
    $CheckpointPath = [IO.Path]::Combine($outputPath, 'checkpoint.json')
}
else {
    $CheckpointPath = [IO.Path]::GetFullPath($CheckpointPath)
}

[string[]]$inputFiles = [IO.Directory]::GetFiles($inputPath, '*.csv', [IO.SearchOption]::TopDirectoryOnly)
[Array]::Sort($inputFiles, [StringComparer]::Ordinal)
if ($inputFiles.Count -eq 0) {
    throw "No CSV files were found in: $inputPath"
}

$inputFingerprint = Get-InputFingerprint -Files $inputFiles
$recordsPath = [IO.Path]::Combine($outputPath, 'records.v2.csv')
$rejectedPath = [IO.Path]::Combine($outputPath, 'rejected-rows.csv')
$auditPath = [IO.Path]::Combine($outputPath, 'audit.jsonl')

if ([IO.File]::Exists($CheckpointPath)) {
    $state = Get-Content -LiteralPath $CheckpointPath -Raw -Encoding utf8 | ConvertFrom-Json
    if ([int]$state.checkpoint_version -ne 1) {
        throw 'Unsupported checkpoint version.'
    }
    if ([string]$state.input_fingerprint -ne $inputFingerprint) {
        throw 'Input files changed after the checkpoint was created.'
    }
    foreach ($requiredOutput in @($recordsPath, $rejectedPath, $auditPath)) {
        if (-not [IO.File]::Exists($requiredOutput)) {
            throw "Checkpoint output is missing: $requiredOutput"
        }
    }
}
else {
    Write-Utf8Text -Path $recordsPath -Text ((ConvertTo-StableCsvLine -Values @(
        'schema_version',
        'record_id',
        'parent_record_id',
        'relative_path',
        'owner_upn',
        'size_bytes',
        'modified_utc',
        'is_deleted'
    )) + "`n")
    Write-Utf8Text -Path $rejectedPath -Text ((ConvertTo-StableCsvLine -Values @(
        'source_file',
        'source_row',
        'error_code',
        'error_message'
    )) + "`n")
    Write-Utf8Text -Path $auditPath -Text ''

    $state = [pscustomobject][ordered]@{
        checkpoint_version = 1
        input_fingerprint = $inputFingerprint
        next_file = 0
        next_row = 0
        accepted_rows = 0
        rejected_rows = 0
        complete = $false
    }
    Write-Checkpoint -Path $CheckpointPath -State $state
}

if ([bool]$state.complete) {
    return [pscustomobject][ordered]@{
        Complete = $true
        AcceptedRows = [int]$state.accepted_rows
        RejectedRows = [int]$state.rejected_rows
    }
}

$errorMessages = @{
    missing_directory_id = 'directory_id is required'
    invalid_path = 'relative_path is required'
    invalid_size_mib = 'size_mib must be a non-negative invariant decimal'
    invalid_modified_local = 'modified_local must use yyyy-MM-dd HH:mm:ss'
    invalid_utc_offset = 'utc_offset_minutes must be an integer from -840 through 840'
    invalid_is_deleted = 'is_deleted must be true, false, 1, or 0'
}

$processedThisRun = 0
for ($fileIndex = [int]$state.next_file; $fileIndex -lt $inputFiles.Count; $fileIndex++) {
    $file = $inputFiles[$fileIndex]
    $rows = @(Import-Csv -LiteralPath $file -Encoding utf8)
    if ($rows.Count -eq 0) {
        throw "Input CSV has no data rows: $([IO.Path]::GetFileName($file))"
    }

    $headers = @($rows[0].PSObject.Properties.Name)
    foreach ($requiredColumn in $RequiredColumns) {
        if ($headers -cnotcontains $requiredColumn) {
            throw "Input CSV $([IO.Path]::GetFileName($file)) is missing column: $requiredColumn"
        }
    }

    $rowStart = if ($fileIndex -eq [int]$state.next_file) { [int]$state.next_row } else { 0 }
    for ($rowIndex = $rowStart; $rowIndex -lt $rows.Count; $rowIndex++) {
        if ($BatchSize -gt 0 -and $processedThisRun -ge $BatchSize) {
            Write-Checkpoint -Path $CheckpointPath -State $state
            return [pscustomobject][ordered]@{
                Complete = $false
                AcceptedRows = [int]$state.accepted_rows
                RejectedRows = [int]$state.rejected_rows
            }
        }

        $row = $rows[$rowIndex]
        $sourceFile = [IO.Path]::GetFileName($file)
        $sourceRow = $rowIndex + 2
        $sourceValues = foreach ($column in $RequiredColumns) { [string]$row.$column }
        $inputHash = Get-TextSha256 -Text (ConvertTo-StableCsvLine -Values $sourceValues)
        $errorCode = $null

        try {
            if ([string]::IsNullOrWhiteSpace([string]$row.directory_id)) {
                $errorCode = 'missing_directory_id'
                throw $errorCode
            }
            $recordId = Get-StableRecordId -DirectoryId ([string]$row.directory_id)
            $parentId = if ([string]::IsNullOrWhiteSpace([string]$row.parent_directory_id)) {
                ''
            }
            else {
                Get-StableRecordId -DirectoryId ([string]$row.parent_directory_id)
            }

            $relativePath = ([string]$row.relative_path).Trim().Replace('\', '/') -replace '/+', '/'
            $relativePath = $relativePath.Trim('/')
            if ([string]::IsNullOrWhiteSpace($relativePath)) {
                $errorCode = 'invalid_path'
                throw $errorCode
            }
            $ownerUpn = ([string]$row.owner_upn).Trim().ToLowerInvariant()

            try {
                $sizeMiB = [decimal]::Parse(([string]$row.size_mib).Trim())
                if ($sizeMiB -lt 0) { throw 'negative size' }
                $sizeBytesDecimal = [decimal]::Round(
                    $sizeMiB * [decimal]1048576,
                    0,
                    [MidpointRounding]::AwayFromZero
                )
                if ($sizeBytesDecimal -gt [long]::MaxValue) { throw 'size overflow' }
                $sizeBytes = [long]$sizeBytesDecimal
            }
            catch {
                $errorCode = 'invalid_size_mib'
                throw $errorCode
            }

            try {
                $modifiedLocal = [datetime]::ParseExact(
                    ([string]$row.modified_local).Trim(),
                    'yyyy-MM-dd HH:mm:ss',
                    $InvariantCulture,
                    [Globalization.DateTimeStyles]::None
                )
            }
            catch {
                $errorCode = 'invalid_modified_local'
                throw $errorCode
            }

            try {
                $offsetMinutes = [int]::Parse(
                    ([string]$row.utc_offset_minutes).Trim(),
                    [Globalization.NumberStyles]::Integer,
                    $InvariantCulture
                )
                if ($offsetMinutes -lt -840 -or $offsetMinutes -gt 840) { throw 'offset range' }
                $offset = [TimeSpan]::FromMinutes($offsetMinutes)
                $modifiedUtc = [DateTimeOffset]::new($modifiedLocal, $offset).ToUniversalTime().ToString(
                    "yyyy-MM-dd'T'HH:mm:ss.fff'Z'",
                    $InvariantCulture
                )
            }
            catch {
                $errorCode = 'invalid_utc_offset'
                throw $errorCode
            }

            switch (([string]$row.is_deleted).Trim().ToLowerInvariant()) {
                'true' { $isDeleted = 'true'; break }
                '1' { $isDeleted = 'true'; break }
                'false' { $isDeleted = 'false'; break }
                '0' { $isDeleted = 'false'; break }
                default {
                    $errorCode = 'invalid_is_deleted'
                    throw $errorCode
                }
            }

            Add-Utf8Line -Path $recordsPath -Line (ConvertTo-StableCsvLine -Values @(
                '2',
                $recordId,
                $parentId,
                $relativePath,
                $ownerUpn,
                $sizeBytes.ToString($InvariantCulture),
                $modifiedUtc,
                $isDeleted
            ))
            Add-Utf8Line -Path $auditPath -Line (Get-StableAuditJson -Event ([ordered]@{
                event = 'accepted'
                source_file = $sourceFile
                source_row = $sourceRow
                record_id = $recordId
                input_sha256 = $inputHash
            }))
            $state.accepted_rows = [int]$state.accepted_rows + 1
        }
        catch {
            if ([string]::IsNullOrWhiteSpace([string]$errorCode)) {
                throw
            }
            Add-Utf8Line -Path $rejectedPath -Line (ConvertTo-StableCsvLine -Values @(
                $sourceFile,
                $sourceRow.ToString($InvariantCulture),
                $errorCode,
                $errorMessages[$errorCode]
            ))
            Add-Utf8Line -Path $auditPath -Line (Get-StableAuditJson -Event ([ordered]@{
                event = 'rejected'
                source_file = $sourceFile
                source_row = $sourceRow
                error_code = $errorCode
                input_sha256 = $inputHash
            }))
            $state.rejected_rows = [int]$state.rejected_rows + 1
        }

        $processedThisRun++
        $state.next_file = $fileIndex
        $state.next_row = $rowIndex + 1
        Write-Checkpoint -Path $CheckpointPath -State $state
    }

    $state.next_file = $fileIndex + 1
    $state.next_row = 0
    Write-Checkpoint -Path $CheckpointPath -State $state
}

$state.complete = $true
Write-Checkpoint -Path $CheckpointPath -State $state
return [pscustomobject][ordered]@{
    Complete = $true
    AcceptedRows = [int]$state.accepted_rows
    RejectedRows = [int]$state.rejected_rows
}
