$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $Root 'VcfNsxGroupGuard/VcfNsxGroupGuard.psd1'
$ModulePath = Join-Path $Root 'VcfNsxGroupGuard/VcfNsxGroupGuard.psm1'
$ContractPath = Join-Path $Root 'docs/contract.json'
$SourcesPath = Join-Path $Root 'docs/official_sources.json'
$MockPath = Join-Path $PSScriptRoot 'mock_nsx_policy.py'
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    'vcf91-0054-' + [guid]::NewGuid().ToString('N')
)
[System.IO.Directory]::CreateDirectory($TempRoot) | Out-Null

function Assert-True {
    param(
        [Parameter(Mandatory)]
        [bool] $Condition,

        [Parameter(Mandatory)]
        [string] $Message
    )

    if (-not $Condition) {
        throw "ASSERTION FAILED: $Message"
    }
}

function Assert-Equal {
    param(
        [AllowNull()]
        $Actual,

        [AllowNull()]
        $Expected,

        [Parameter(Mandatory)]
        [string] $Message
    )

    if ($Actual -cne $Expected) {
        throw "ASSERTION FAILED: $Message (expected '$Expected', got '$Actual')"
    }
}

function Get-ExpectedAuthorization {
    param(
        [Parameter(Mandatory)]
        [string] $Username,

        [Parameter(Mandatory)]
        [string] $Password
    )

    return 'Basic ' + [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes("${Username}:${Password}")
    )
}

function Get-ExpectedPath {
    param(
        [Parameter(Mandatory)]
        [string] $DomainId,

        [Parameter(Mandatory)]
        [string] $GroupId
    )

    $EncodedDomain = [uri]::EscapeDataString($DomainId)
    $EncodedGroup = [uri]::EscapeDataString($GroupId)
    return "/policy/api/v1/infra/domains/$EncodedDomain/groups/$EncodedGroup"
}

function Assert-ReadWireShape {
    param(
        [Parameter(Mandatory)]
        $Entry,

        [Parameter(Mandatory)]
        $Case
    )

    Assert-Equal $Entry.operationId 'ReadGroupForDomain' `
        "$($Case.Name) read operationId"
    Assert-Equal $Entry.method 'GET' "$($Case.Name) read method"
    Assert-Equal $Entry.path (
        Get-ExpectedPath `
            -DomainId $Case.DomainId `
            -GroupId $Case.GroupId
    ) "$($Case.Name) escaped read path"
    Assert-Equal $Entry.rawQuery '' "$($Case.Name) read query"
    Assert-Equal $Entry.authorization (
        Get-ExpectedAuthorization `
            -Username $Case.Username `
            -Password $Case.Password
    ) "$($Case.Name) read authorization"
    Assert-True ($Entry.accept -like 'application/json*') `
        "$($Case.Name) read Accept header"
    Assert-True ($null -eq $Entry.contentType) `
        "$($Case.Name) read omits Content-Type"
    Assert-Equal $Entry.contentLength 0 "$($Case.Name) read body length"
    Assert-Equal $Entry.bodyHex '' "$($Case.Name) read body bytes"
    Assert-Equal $Entry.body '' "$($Case.Name) read body"
}

function Assert-UpdateWireShape {
    param(
        [Parameter(Mandatory)]
        $Entry,

        [Parameter(Mandatory)]
        $Case,

        [Parameter(Mandatory)]
        [bool] $HasDescription
    )

    Assert-Equal $Entry.operationId 'UpdateGroupForDomain' `
        "$($Case.Name) update operationId"
    Assert-Equal $Entry.method 'PUT' "$($Case.Name) update method"
    Assert-Equal $Entry.path (
        Get-ExpectedPath `
            -DomainId $Case.DomainId `
            -GroupId $Case.GroupId
    ) "$($Case.Name) escaped update path"
    Assert-Equal $Entry.rawQuery '' "$($Case.Name) update query"
    Assert-Equal $Entry.authorization (
        Get-ExpectedAuthorization `
            -Username $Case.Username `
            -Password $Case.Password
    ) "$($Case.Name) update authorization"
    Assert-True ($Entry.accept -like 'application/json*') `
        "$($Case.Name) update Accept header"
    Assert-True ($Entry.contentType -like 'application/json*') `
        "$($Case.Name) update Content-Type"
    Assert-True ($Entry.contentLength -gt 0) `
        "$($Case.Name) update body is present"

    $BodyBytes = [Convert]::FromHexString([string] $Entry.bodyHex)
    Assert-Equal $Entry.contentLength $BodyBytes.Length `
        "$($Case.Name) Content-Length matches exact body bytes"
    Assert-Equal ([Text.Encoding]::UTF8.GetString($BodyBytes)) $Entry.body `
        "$($Case.Name) update body is exact UTF-8"
    Assert-True (
        $BodyBytes.Length -lt 3 -or
        -not (
            $BodyBytes[0] -eq 0xEF -and
            $BodyBytes[1] -eq 0xBB -and
            $BodyBytes[2] -eq 0xBF
        )
    ) "$($Case.Name) update body has no UTF-8 BOM"

    $Body = $Entry.body | ConvertFrom-Json
    $ActualMembers = @($Body.PSObject.Properties.Name | Sort-Object)
    $ExpectedMembers = if ($HasDescription) {
        @('_revision', 'description', 'display_name')
    }
    else {
        @('_revision', 'display_name')
    }
    Assert-Equal ($ActualMembers -join ',') ($ExpectedMembers -join ',') `
        "$($Case.Name) exact JSON member set"
    Assert-Equal ([int] $Body._revision) $Case.CurrentRevision `
        "$($Case.Name) update revision"
    Assert-Equal $Body.display_name $Case.DesiredDisplayName `
        "$($Case.Name) update display_name"
    if ($HasDescription) {
        Assert-Equal $Body.description $Case.Description `
            "$($Case.Name) update description"
    }
    else {
        Assert-True (
            $Body.PSObject.Properties.Name -cnotcontains 'description'
        ) "$($Case.Name) omits unbound description"
    }
}

function Invoke-GuardCase {
    param(
        [Parameter(Mandatory)]
        [string] $Name,

        [Parameter(Mandatory)]
        [string] $Username,

        [Parameter(Mandatory)]
        [string] $Password,

        [Parameter(Mandatory)]
        [string] $DomainId,

        [Parameter(Mandatory)]
        [string] $GroupId,

        [Parameter(Mandatory)]
        [int32] $CurrentRevision,

        [Parameter(Mandatory)]
        [string] $CurrentDisplayName,

        [Parameter(Mandatory)]
        [int32] $ExpectedRevision,

        [Parameter(Mandatory)]
        [string] $ExpectedDisplayName,

        [Parameter(Mandatory)]
        [string] $DesiredDisplayName,

        [switch] $IncludeDescription,

        [string] $Description
    )

    $CaseRoot = Join-Path $TempRoot $Name
    [System.IO.Directory]::CreateDirectory($CaseRoot) | Out-Null
    $PortPath = Join-Path $CaseRoot 'port.txt'
    $LogPath = Join-Path $CaseRoot 'requests.jsonl'
    $ScenarioPath = Join-Path $CaseRoot 'scenario.json'
    $StdoutPath = Join-Path $CaseRoot 'mock.stdout'
    $StderrPath = Join-Path $CaseRoot 'mock.stderr'
    $MockProcess = $null
    $HttpClient = $null
    $HttpHandler = $null

    $Scenario = [ordered]@{
        username = $Username
        password = $Password
        domain_id = $DomainId
        group_id = $GroupId
        current_revision = $CurrentRevision
        current_display_name = $CurrentDisplayName
    }
    [System.IO.File]::WriteAllText(
        $ScenarioPath,
        ($Scenario | ConvertTo-Json -Depth 5 -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )

    try {
        $MockProcess = Start-Process -FilePath 'python3' -ArgumentList @(
            '-B',
            $MockPath,
            $PortPath,
            $LogPath,
            $ContractPath,
            $ScenarioPath
        ) -PassThru -RedirectStandardOutput $StdoutPath `
          -RedirectStandardError $StderrPath

        $Deadline = [System.Diagnostics.Stopwatch]::StartNew()
        while (-not (Test-Path -LiteralPath $PortPath)) {
            if ($MockProcess.HasExited) {
                $MockError = Get-Content -Raw -LiteralPath $StderrPath
                throw "Loopback mock exited before startup: $MockError"
            }
            if ($Deadline.Elapsed.TotalSeconds -gt 10) {
                throw "Timed out waiting for loopback mock startup for $Name."
            }
            Start-Sleep -Milliseconds 25
        }
        $Port = [int](Get-Content -Raw -LiteralPath $PortPath)

        $Configuration = [VMware.Binding.OpenApi.Client.Configuration]::new()
        $Configuration.BasePath = "http://127.0.0.1:$Port/policy/api/v1"
        $Configuration.Username = $Username
        $Configuration.Password = ConvertTo-SecureString `
            $Password -AsPlainText -Force
        $HttpHandler = [System.Net.Http.HttpClientHandler]::new()
        $HttpClient = [System.Net.Http.HttpClient]::new($HttpHandler, $false)
        $PolicyApi = [VMware.Bindings.Nsx.Policy.Api.PolicyApi]::new(
            $HttpClient,
            $Configuration,
            $HttpHandler
        )

        $Arguments = @{
            PolicyApi = $PolicyApi
            DomainId = $DomainId
            GroupId = $GroupId
            ExpectedRevision = $ExpectedRevision
            ExpectedDisplayName = $ExpectedDisplayName
            DisplayName = $DesiredDisplayName
        }
        if ($IncludeDescription) {
            $Arguments.Description = $Description
        }

        $Result = $null
        $Thrown = $null
        try {
            $Result = Set-VcfNsxGroupDisplayName @Arguments
        }
        catch {
            $Thrown = $_.Exception
        }

        Start-Sleep -Milliseconds 50
        $Entries = @(
            Get-Content -LiteralPath $LogPath |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                ForEach-Object { $_ | ConvertFrom-Json }
        )

        return [pscustomobject]@{
            Name = $Name
            Username = $Username
            Password = $Password
            DomainId = $DomainId
            GroupId = $GroupId
            CurrentRevision = $CurrentRevision
            CurrentDisplayName = $CurrentDisplayName
            ExpectedRevision = $ExpectedRevision
            ExpectedDisplayName = $ExpectedDisplayName
            DesiredDisplayName = $DesiredDisplayName
            IncludeDescription = [bool] $IncludeDescription
            Description = $Description
            Result = $Result
            Error = $Thrown
            Entries = $Entries
        }
    }
    finally {
        if ($null -ne $HttpClient) {
            $HttpClient.Dispose()
        }
        if ($null -ne $HttpHandler) {
            $HttpHandler.Dispose()
        }
        if ($null -ne $MockProcess -and -not $MockProcess.HasExited) {
            Stop-Process -Id $MockProcess.Id -Force -ErrorAction SilentlyContinue
            $MockProcess.WaitForExit()
        }
    }
}

try {
    $Sources = Get-Content -Raw -LiteralPath $SourcesPath | ConvertFrom-Json
    $Contract = Get-Content -Raw -LiteralPath $ContractPath | ConvertFrom-Json
    Assert-Equal $Sources.repository 'vmware/vcf-api-specs' `
        'official repository'
    Assert-Equal $Sources.repository_commit_sha `
        '3949fc33339fc5ea1b77eadb258f1cf49aa88e26' `
        'pinned VCF 9.1 repository commit'
    Assert-Equal $Sources.spec_path `
        'specifications/nsx/openapi-2.0/nsx_policy_api.yaml' `
        'official NSX Policy specification path'
    Assert-Equal $Sources.license 'Apache-2.0' 'official source license'
    Assert-Equal $Contract.source.repository_commit_sha `
        $Sources.repository_commit_sha 'contract commit provenance'
    Assert-Equal $Contract.source.spec_path $Sources.spec_path `
        'contract path provenance'

    $ExpectedOperationIds = @(
        'ReadGroupForDomain',
        'UpdateGroupForDomain'
    )
    Assert-Equal @($Contract.operations).Count 2 'contract operation count'
    Assert-Equal @($Sources.operations).Count 2 'source operation count'
    Assert-Equal (@($Contract.operations.operationId) -join ',') `
        ($ExpectedOperationIds -join ',') 'contract operationIds'
    Assert-Equal (@($Sources.operations.operationId) -join ',') `
        ($ExpectedOperationIds -join ',') 'source operationIds'
    foreach ($SourceOperation in @($Sources.operations)) {
        Assert-Equal $SourceOperation.repository_commit_sha `
            $Sources.repository_commit_sha `
            "$($SourceOperation.operationId) commit provenance"
        Assert-Equal $SourceOperation.spec_path $Sources.spec_path `
            "$($SourceOperation.operationId) path provenance"
    }
    Assert-Equal $Contract.operations[0].method 'GET' 'read method contract'
    Assert-Equal $Contract.operations[1].method 'PUT' 'update method contract'
    Assert-Equal $Contract.operations[0].path `
        '/policy/api/v1/infra/domains/{domain-id}/groups/{group-id}' `
        'read path contract'
    Assert-Equal $Contract.operations[1].path `
        $Contract.operations[0].path 'update path contract'
    Assert-Equal $Contract.serializationRule.unsetOptionalProperties `
        'omit' 'optional serialization contract'

    $Manifest = Import-PowerShellDataFile -LiteralPath $ManifestPath
    Assert-Equal @($Manifest.FunctionsToExport).Count 1 `
        'manifest export count'
    Assert-Equal $Manifest.FunctionsToExport[0] `
        'Set-VcfNsxGroupDisplayName' 'manifest export'
    Assert-Equal @($Manifest.RequiredModules).Count 1 `
        'required module count'
    Assert-Equal $Manifest.RequiredModules[0].ModuleName `
        'VMware.Sdk.Vcf.SddcManager' 'VCF SDK prerequisite'
    Assert-Equal ([version] $Manifest.RequiredModules[0].ModuleVersion) `
        ([version] '13.5.0.25380678') 'VCF SDK prerequisite version'

    $SourceText = Get-Content -Raw -LiteralPath $ModulePath
    foreach ($Forbidden in @(
        'Invoke-RestMethod',
        'Invoke-WebRequest',
        'System.Net.Http',
        'HttpClient',
        'curl',
        'Start-Process'
    )) {
        Assert-True (-not $SourceText.Contains($Forbidden)) `
            "production module must not contain $Forbidden"
    }
    Assert-True $SourceText.Contains('ReadGroupForDomain') `
        'production module uses ReadGroupForDomain'
    Assert-True $SourceText.Contains('UpdateGroupForDomain') `
        'production module uses UpdateGroupForDomain'

    $Vendored = @(
        Get-ChildItem -LiteralPath $Root -Recurse -File |
            Where-Object {
                $_.Extension -in @('.dll', '.nupkg') -or
                $_.Name -match '^VMware\..*\.(psd1|psm1)$'
            }
    )
    Assert-Equal $Vendored.Count 0 'no VMware SDK is vendored'

    $Sdk = Get-Module -ListAvailable -Name VMware.Sdk.Vcf.SddcManager |
        Where-Object Version -GE ([version] '13.5.0.25380678') |
        Sort-Object Version -Descending |
        Select-Object -First 1
    Assert-True ($null -ne $Sdk) `
        'VCF PowerCLI 9.1 prerequisite is installed'
    Import-Module $Sdk.Path -ErrorAction Stop
    Import-Module $ManifestPath -Force -ErrorAction Stop

    $Exports = @((Get-Command -Module VcfNsxGroupGuard).Name)
    Assert-Equal $Exports.Count 1 'runtime export count'
    Assert-Equal $Exports[0] 'Set-VcfNsxGroupDisplayName' `
        'runtime export'

    $RunId = [guid]::NewGuid().ToString('N')
    $BaseRevision = 10 + (
        [Convert]::ToInt32($RunId.Substring(0, 2), 16)
    )
    $DomainId = 'domain ' + $RunId.Substring(2, 6) + '/west?'
    $GroupId = 'group+' + $RunId.Substring(8, 6) + '/blue?'
    $CurrentName = 'current-' + $RunId.Substring(14, 6)
    $DesiredName = 'desired-Δ-' + $RunId.Substring(20, 6)
    $Username = 'fixture-user-' + $RunId.Substring(0, 8)
    $Password = 'fixture-pass-' + $RunId.Substring(8, 10)

    $RevisionFailure = Invoke-GuardCase `
        -Name 'revision-failure' `
        -Username $Username `
        -Password $Password `
        -DomainId $DomainId `
        -GroupId $GroupId `
        -CurrentRevision $BaseRevision `
        -CurrentDisplayName $CurrentName `
        -ExpectedRevision ($BaseRevision + 1) `
        -ExpectedDisplayName $CurrentName `
        -DesiredDisplayName $DesiredName
    Assert-True ($null -ne $RevisionFailure.Error) `
        'revision mismatch throws'
    Assert-True ($null -eq $RevisionFailure.Result) `
        'revision mismatch returns no result'
    Assert-Equal @($RevisionFailure.Entries).Count 1 `
        'revision mismatch logs only the precheck'
    Assert-ReadWireShape `
        -Entry $RevisionFailure.Entries[0] `
        -Case $RevisionFailure

    $NameFailure = Invoke-GuardCase `
        -Name 'name-failure' `
        -Username $Username `
        -Password $Password `
        -DomainId $DomainId `
        -GroupId $GroupId `
        -CurrentRevision $BaseRevision `
        -CurrentDisplayName $CurrentName `
        -ExpectedRevision $BaseRevision `
        -ExpectedDisplayName ('wrong-' + $RunId.Substring(26, 6)) `
        -DesiredDisplayName $DesiredName
    Assert-True ($null -ne $NameFailure.Error) 'name mismatch throws'
    Assert-True ($null -eq $NameFailure.Result) `
        'name mismatch returns no result'
    Assert-Equal @($NameFailure.Entries).Count 1 `
        'name mismatch logs only the precheck'
    Assert-ReadWireShape -Entry $NameFailure.Entries[0] -Case $NameFailure

    $OmittedDescription = Invoke-GuardCase `
        -Name 'omitted-description' `
        -Username $Username `
        -Password $Password `
        -DomainId $DomainId `
        -GroupId $GroupId `
        -CurrentRevision $BaseRevision `
        -CurrentDisplayName $CurrentName `
        -ExpectedRevision $BaseRevision `
        -ExpectedDisplayName $CurrentName `
        -DesiredDisplayName $DesiredName
    Assert-True ($null -eq $OmittedDescription.Error) `
        'passing precheck with omitted description succeeds'
    Assert-Equal @($OmittedDescription.Entries).Count 2 `
        'passing precheck performs exactly one read and one update'
    Assert-ReadWireShape `
        -Entry $OmittedDescription.Entries[0] `
        -Case $OmittedDescription
    Assert-UpdateWireShape `
        -Entry $OmittedDescription.Entries[1] `
        -Case $OmittedDescription `
        -HasDescription $false
    Assert-True (
        $OmittedDescription.Result -is
            [VMware.Bindings.Nsx.Policy.Model.Group]
    ) 'update returns the generated Group response'
    Assert-Equal $OmittedDescription.Result.Id $GroupId `
        'returned group ID'
    Assert-Equal $OmittedDescription.Result.DisplayName $DesiredName `
        'returned group display name'
    Assert-Equal ([int] $OmittedDescription.Result.Revision) `
        ($BaseRevision + 1) 'returned group revision'
    Assert-True ($null -eq $OmittedDescription.Result.Description) `
        'returned group leaves omitted description unset'

    $ExplicitDescription = 'description-café-' + $RunId.Substring(4, 12)
    $WithDescription = Invoke-GuardCase `
        -Name 'explicit-description' `
        -Username $Username `
        -Password $Password `
        -DomainId $DomainId `
        -GroupId $GroupId `
        -CurrentRevision $BaseRevision `
        -CurrentDisplayName $CurrentName `
        -ExpectedRevision $BaseRevision `
        -ExpectedDisplayName $CurrentName `
        -DesiredDisplayName $DesiredName `
        -IncludeDescription `
        -Description $ExplicitDescription
    Assert-True ($null -eq $WithDescription.Error) `
        'passing precheck with explicit description succeeds'
    Assert-Equal @($WithDescription.Entries).Count 2 `
        'explicit description performs one read and one update'
    Assert-ReadWireShape `
        -Entry $WithDescription.Entries[0] `
        -Case $WithDescription
    Assert-UpdateWireShape `
        -Entry $WithDescription.Entries[1] `
        -Case $WithDescription `
        -HasDescription $true
    Assert-Equal $WithDescription.Result.Description $ExplicitDescription `
        'returned group preserves explicit description'

    Write-Output 'all checks passed'
}
finally {
    Remove-Module VcfNsxGroupGuard -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
}
