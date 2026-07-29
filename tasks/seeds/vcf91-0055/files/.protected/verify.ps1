$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $Root (
    'VcfNsxCredentialGate/VcfNsxCredentialGate.psd1'
)
$ModulePath = Join-Path $Root (
    'VcfNsxCredentialGate/VcfNsxCredentialGate.psm1'
)
$ContractPath = Join-Path $Root 'docs/contract.json'
$SourcesPath = Join-Path $Root 'docs/official_sources.json'
$MockPath = Join-Path $PSScriptRoot 'mock_nsx_policy.py'
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    'vcf91-0055-' + [guid]::NewGuid().ToString('N')
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

function Wait-Until {
    param(
        [Parameter(Mandatory)]
        [scriptblock] $Condition,

        [Parameter(Mandatory)]
        [string] $Message,

        [int] $TimeoutSeconds = 10
    )

    $Timer = [System.Diagnostics.Stopwatch]::StartNew()
    while (-not (& $Condition)) {
        if ($Timer.Elapsed.TotalSeconds -gt $TimeoutSeconds) {
            throw "Timed out: $Message"
        }
        Start-Sleep -Milliseconds 20
    }
}

function Get-RequestEntries {
    param(
        [Parameter(Mandatory)]
        [string] $Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    Get-Content -LiteralPath $Path |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        ForEach-Object { $_ | ConvertFrom-Json }
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

function New-PolicyApiBundle {
    param(
        [Parameter(Mandatory)]
        [int] $Port,

        [Parameter(Mandatory)]
        [string] $Username,

        [Parameter(Mandatory)]
        [string] $Password
    )

    $Configuration = [VMware.Binding.OpenApi.Client.Configuration]::new()
    $Configuration.BasePath = "http://127.0.0.1:$Port/policy/api/v1"
    $Configuration.Username = $Username
    $Configuration.Password = ConvertTo-SecureString `
        $Password -AsPlainText -Force
    $Handler = [System.Net.Http.HttpClientHandler]::new()
    $Client = [System.Net.Http.HttpClient]::new($Handler, $false)
    $Api = [VMware.Bindings.Nsx.Policy.Api.PolicyApi]::new(
        $Client,
        $Configuration,
        $Handler
    )
    return [pscustomobject]@{
        Api = $Api
        Client = $Client
        Handler = $Handler
    }
}

function Start-ModuleCall {
    param(
        [Parameter(Mandatory)]
        [string] $CommandName,

        [Parameter(Mandatory)]
        [hashtable] $Parameters
    )

    $Runner = [PowerShell]::Create()
    $null = $Runner.AddScript({
        param($Manifest, $Name, $Arguments)

        $ErrorActionPreference = 'Stop'
        $ProgressPreference = 'SilentlyContinue'
        $WarningPreference = 'SilentlyContinue'
        Import-Module $Manifest -Force -ErrorAction Stop
        & $Name @Arguments
    }).AddArgument($ManifestPath).AddArgument(
        $CommandName
    ).AddArgument($Parameters)
    $Handle = $Runner.BeginInvoke()
    return [pscustomobject]@{
        PowerShell = $Runner
        Handle = $Handle
    }
}

function Complete-ModuleCall {
    param(
        [Parameter(Mandatory)]
        [object] $Call,

        [Parameter(Mandatory)]
        [string] $Message
    )

    $Runner = $Call.PowerShell
    try {
        if (-not $Call.Handle.AsyncWaitHandle.WaitOne(15000)) {
            throw "Timed out: $Message"
        }
        $Output = $Runner.EndInvoke($Call.Handle)
        return [pscustomobject]@{
            Items = @($Output)
        }
    }
    finally {
        $Runner.Dispose()
        $Call.PowerShell = $null
    }
}

function Assert-RequestWireShape {
    param(
        [Parameter(Mandatory)]
        $Entry,

        [Parameter(Mandatory)]
        [string] $DomainId,

        [Parameter(Mandatory)]
        [int] $Port,

        [Parameter(Mandatory)]
        [string] $Username,

        [Parameter(Mandatory)]
        [string] $Password,

        [Parameter(Mandatory)]
        [string] $Phase
    )

    Assert-Equal $Entry.operationId 'ListGroupForDomain' `
        "$Phase operationId"
    Assert-Equal $Entry.method 'GET' "$Phase method"
    $EncodedDomain = [uri]::EscapeDataString($DomainId)
    Assert-Equal $Entry.path (
        "/policy/api/v1/infra/domains/$EncodedDomain/groups"
    ) "$Phase escaped path"
    Assert-Equal $Entry.rawQuery '' `
        "$Phase omits all seven optional query parameters"
    Assert-Equal $Entry.host "127.0.0.1:$Port" "$Phase Host header"
    Assert-Equal $Entry.authorization (
        Get-ExpectedAuthorization -Username $Username -Password $Password
    ) "$Phase authorization"
    Assert-True ($Entry.accept -like 'application/json*') `
        "$Phase Accept header"
    Assert-True ($null -eq $Entry.contentType) `
        "$Phase omits Content-Type"
    Assert-Equal $Entry.contentLength 0 "$Phase body length"
    Assert-Equal $Entry.bodyHex '' "$Phase exact body bytes"
    Assert-Equal $Entry.body '' "$Phase body"
}

$MockProcess = $null
$OldBundle = $null
$NewBundle = $null
$OldCall = $null
$RotationCall = $null
$NewCall = $null

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
    Assert-Equal $Contract.source.spec_blob_sha $Sources.spec_blob_sha `
        'contract blob provenance'

    Assert-Equal @($Contract.operations).Count 1 'contract operation count'
    Assert-Equal @($Sources.operations).Count 1 'source operation count'
    Assert-Equal $Contract.operations[0].operationId `
        'ListGroupForDomain' 'contract operationId'
    Assert-Equal $Sources.operations[0].operationId `
        'ListGroupForDomain' 'official source operationId'
    Assert-Equal $Sources.operations[0].repository_commit_sha `
        $Sources.repository_commit_sha 'operation commit provenance'
    Assert-Equal $Sources.operations[0].spec_path $Sources.spec_path `
        'operation spec-path provenance'
    Assert-Equal $Contract.operations[0].method 'GET' 'contract method'
    Assert-Equal $Contract.operations[0].path `
        '/policy/api/v1/infra/domains/{domain-id}/groups' `
        'contract route'
    Assert-Equal @($Contract.operations[0].parameters).Count 8 `
        'contract parameter count'
    Assert-Equal (
        @($Contract.operations[0].parameters.name) -join ','
    ) (
        @(
            'domain-id',
            'cursor',
            'include_mark_for_delete_objects',
            'included_fields',
            'member_types',
            'page_size',
            'sort_ascending',
            'sort_by'
        ) -join ','
    ) 'contract parameter order'
    foreach ($Optional in @($Contract.operations[0].parameters)[1..7]) {
        Assert-True ($Optional.required -eq $false) `
            "$($Optional.name) is optional"
        Assert-True ($Optional.omitWhenUnset -eq $true) `
            "$($Optional.name) is omitted when unset"
    }
    Assert-Equal `
        $Contract.serializationRule.unsetOptionalQueryParameters `
        'omit' 'unset optional query serialization'

    $Manifest = Import-PowerShellDataFile -LiteralPath $ManifestPath
    $ExpectedExports = @(
        'New-VcfNsxCredentialGate',
        'Get-VcfNsxGroupPage',
        'Set-VcfNsxCredential'
    )
    Assert-Equal @($Manifest.FunctionsToExport).Count 3 `
        'manifest export count'
    Assert-Equal (@($Manifest.FunctionsToExport) -join ',') `
        ($ExpectedExports -join ',') 'manifest exports'
    Assert-Equal @($Manifest.RequiredModules).Count 1 `
        'required module count'
    Assert-Equal $Manifest.RequiredModules[0].ModuleName `
        'VMware.Sdk.Vcf.SddcManager' 'VCF SDK prerequisite'
    Assert-Equal ([version] $Manifest.RequiredModules[0].ModuleVersion) `
        ([version] '13.5.0.25380678') `
        'VCF SDK prerequisite version'

    $SourceText = Get-Content -Raw -LiteralPath $ModulePath
    foreach ($Forbidden in @(
        'Invoke-RestMethod',
        'Invoke-WebRequest',
        'System.Net.Http',
        'HttpClient',
        'curl',
        'Start-Process',
        'Add-Type'
    )) {
        Assert-True (-not $SourceText.Contains($Forbidden)) `
            "production module must not contain $Forbidden"
    }
    Assert-True $SourceText.Contains(
        'VMware.Bindings.Nsx.Policy.Api.PolicyApi'
    ) 'production module uses the generated PolicyApi type'
    Assert-True $SourceText.Contains('ListGroupForDomain') `
        'production module uses ListGroupForDomain'
    Assert-True $SourceText.Contains(
        'System.Management.Automation.Language.NullString'
    ) 'production module preserves unset string query arguments'
    Assert-True $SourceText.Contains('EnterReadLock') `
        'production module acquires a request lease'
    Assert-True $SourceText.Contains('EnterWriteLock') `
        'production module serializes credential cutover'

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

    $Exports = @(
        (Get-Command -Module VcfNsxCredentialGate).Name |
            Sort-Object
    )
    Assert-Equal $Exports.Count 3 'runtime export count'
    Assert-Equal ($Exports -join ',') (
        @($ExpectedExports | Sort-Object) -join ','
    ) 'runtime exports'

    $RunId = [guid]::NewGuid().ToString('N')
    $DomainId = 'domain ' + $RunId.Substring(0, 7) + '/west?+'
    $OldUsername = 'old-user-' + $RunId.Substring(7, 8)
    $OldPassword = 'old-secret-' + $RunId.Substring(15, 9)
    $NewUsername = 'new-user-' + $RunId.Substring(3, 8)
    $NewPassword = 'new-secret-' + $RunId.Substring(12, 9)
    $OldGroupId = 'old-group-' + $RunId.Substring(20, 6)
    $NewGroupId = 'new-group-' + $RunId.Substring(26, 6)
    $OldDisplayName = 'old-visible-' + $RunId.Substring(1, 6)
    $NewDisplayName = 'new-visible-Δ-' + $RunId.Substring(9, 6)

    $PortPath = Join-Path $TempRoot 'port.txt'
    $LogPath = Join-Path $TempRoot 'requests.jsonl'
    $ScenarioPath = Join-Path $TempRoot 'scenario.json'
    $ReleasePath = Join-Path $TempRoot 'release-old-request'
    $StdoutPath = Join-Path $TempRoot 'mock.stdout'
    $StderrPath = Join-Path $TempRoot 'mock.stderr'
    $Scenario = [ordered]@{
        domain_id = $DomainId
        old_username = $OldUsername
        old_password = $OldPassword
        new_username = $NewUsername
        new_password = $NewPassword
        old_group_id = $OldGroupId
        old_display_name = $OldDisplayName
        new_group_id = $NewGroupId
        new_display_name = $NewDisplayName
    }
    [System.IO.File]::WriteAllText(
        $ScenarioPath,
        ($Scenario | ConvertTo-Json -Depth 5 -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )

    $MockProcess = Start-Process -FilePath 'python3' -ArgumentList @(
        '-B',
        $MockPath,
        $PortPath,
        $LogPath,
        $ContractPath,
        $ScenarioPath,
        $ReleasePath
    ) -PassThru -RedirectStandardOutput $StdoutPath `
      -RedirectStandardError $StderrPath
    Wait-Until -Message 'loopback mock startup' -Condition {
        if ($MockProcess.HasExited) {
            $MockError = Get-Content -Raw -LiteralPath $StderrPath
            throw "Loopback mock exited before startup: $MockError"
        }
        Test-Path -LiteralPath $PortPath
    }
    $Port = [int](Get-Content -Raw -LiteralPath $PortPath)

    $OldBundle = New-PolicyApiBundle `
        -Port $Port -Username $OldUsername -Password $OldPassword
    $NewBundle = New-PolicyApiBundle `
        -Port $Port -Username $NewUsername -Password $NewPassword
    $Gate = New-VcfNsxCredentialGate -PolicyApi $OldBundle.Api
    $IndependentGate = New-VcfNsxCredentialGate -PolicyApi $OldBundle.Api
    Assert-True (-not [object]::ReferenceEquals(
        $Gate,
        $IndependentGate
    )) 'new gates are distinct objects'
    Assert-True (-not [object]::ReferenceEquals(
        $Gate.Lock,
        $IndependentGate.Lock
    )) 'new gates have independent synchronization'

    $OldCall = Start-ModuleCall `
        -CommandName 'Get-VcfNsxGroupPage' `
        -Parameters @{
            Gate = $Gate
            DomainId = $DomainId
        }
    Wait-Until -Message 'old request reaches the loopback service' -Condition {
        @(Get-RequestEntries -Path $LogPath).Count -eq 1
    }
    Assert-True ($Gate.Lock.CurrentReadCount -eq 1) `
        'old request holds one request lease'

    $RotationCall = Start-ModuleCall `
        -CommandName 'Set-VcfNsxCredential' `
        -Parameters @{
            Gate = $Gate
            PolicyApi = $NewBundle.Api
        }
    Wait-Until -Message 'credential cutover waits for old request' -Condition {
        $Gate.Lock.WaitingWriteCount -eq 1
    }
    Assert-True (-not $RotationCall.Handle.IsCompleted) `
        'credential cutover has not published the new client early'
    Assert-True ([object]::ReferenceEquals(
        $Gate.PolicyApi,
        $OldBundle.Api
    )) 'old client remains published while its request is active'

    $NewCall = Start-ModuleCall `
        -CommandName 'Get-VcfNsxGroupPage' `
        -Parameters @{
            Gate = $Gate
            DomainId = $DomainId
        }
    Wait-Until -Message 'later request queues behind cutover' -Condition {
        $Gate.Lock.WaitingReadCount -eq 1
    }
    Start-Sleep -Milliseconds 150
    Assert-Equal @(Get-RequestEntries -Path $LogPath).Count 1 `
        'no later request reaches the server before old lease drains'
    Assert-True (-not $NewCall.Handle.IsCompleted) `
        'later request remains queued'

    [System.IO.File]::WriteAllText(
        $ReleasePath,
        'release',
        [System.Text.UTF8Encoding]::new($false)
    )
    $OldCompletion = Complete-ModuleCall `
        -Call $OldCall -Message 'old request completion'
    $OldCall = $null
    $RotationCompletion = Complete-ModuleCall `
        -Call $RotationCall -Message 'credential cutover completion'
    $RotationCall = $null
    $NewCompletion = Complete-ModuleCall `
        -Call $NewCall -Message 'new request completion'
    $NewCall = $null

    Assert-Equal $OldCompletion.Items.Count 1 `
        'old request returns one generated page'
    Assert-Equal $RotationCompletion.Items.Count 0 `
        'credential cutover returns no object'
    Assert-Equal $NewCompletion.Items.Count 1 `
        'new request returns one generated page'
    $OldResult = $OldCompletion.Items[0]
    $NewResult = $NewCompletion.Items[0]
    Assert-True (
        $OldResult -is
            [VMware.Bindings.Nsx.Policy.Model.GroupListResult]
    ) 'old request returns the generated GroupListResult'
    Assert-True (
        $NewResult -is
            [VMware.Bindings.Nsx.Policy.Model.GroupListResult]
    ) 'new request returns the generated GroupListResult'
    Assert-Equal @($OldResult.Results).Count 1 'old result count'
    Assert-Equal $OldResult.Results[0].Id $OldGroupId `
        'old request completes on the old credential'
    Assert-Equal $OldResult.Results[0].DisplayName $OldDisplayName `
        'old response remains intact'
    Assert-Equal @($NewResult.Results).Count 1 'new result count'
    Assert-Equal $NewResult.Results[0].Id $NewGroupId `
        'queued request uses the new credential'
    Assert-Equal $NewResult.Results[0].DisplayName $NewDisplayName `
        'new response remains intact'
    Assert-True ([object]::ReferenceEquals(
        $Gate.PolicyApi,
        $NewBundle.Api
    )) 'new client is atomically published after the drain'

    $Entries = @(Get-RequestEntries -Path $LogPath)
    Assert-Equal $Entries.Count 2 `
        'exactly one old and one new contract request are logged'
    Assert-RequestWireShape `
        -Entry $Entries[0] `
        -DomainId $DomainId `
        -Port $Port `
        -Username $OldUsername `
        -Password $OldPassword `
        -Phase 'old request'
    Assert-RequestWireShape `
        -Entry $Entries[1] `
        -DomainId $DomainId `
        -Port $Port `
        -Username $NewUsername `
        -Password $NewPassword `
        -Phase 'new request'

    Write-Output 'all checks passed'
}
finally {
    foreach ($Call in @($OldCall, $RotationCall, $NewCall)) {
        if ($null -ne $Call -and $null -ne $Call.PowerShell) {
            try {
                $Call.PowerShell.Stop()
            }
            catch {
            }
            $Call.PowerShell.Dispose()
        }
    }
    if ($null -ne $OldBundle) {
        $OldBundle.Client.Dispose()
        $OldBundle.Handler.Dispose()
    }
    if ($null -ne $NewBundle) {
        $NewBundle.Client.Dispose()
        $NewBundle.Handler.Dispose()
    }
    if ($null -ne $MockProcess -and -not $MockProcess.HasExited) {
        Stop-Process -Id $MockProcess.Id -Force -ErrorAction SilentlyContinue
        $MockProcess.WaitForExit()
    }
    Remove-Module VcfNsxCredentialGate -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
}
