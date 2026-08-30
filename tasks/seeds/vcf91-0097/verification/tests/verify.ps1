Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

class TestVcfSdkConnection {
    [uri] $ServerUri
    [Net.Http.HttpClient] $HttpClient
    [int] $GetClientCalls = 0

    TestVcfSdkConnection(
        [uri] $ServerUri,
        [Net.Http.HttpClient] $HttpClient
    ) {
        $this.ServerUri = $ServerUri
        $this.HttpClient = $HttpClient
    }

    [Net.Http.HttpClient] GetClient() {
        $this.GetClientCalls++
        return $this.HttpClient
    }
}

function Assert-True {
    param(
        [bool] $Condition,

        [Parameter(Mandatory)]
        [string] $Message
    )

    if (-not $Condition) {
        throw "ASSERTION FAILED: $Message"
    }
}

function Assert-Throws {
    param(
        [Parameter(Mandatory)]
        [scriptblock] $Action,

        [Parameter(Mandatory)]
        [string] $Message
    )

    $Threw = $false
    try {
        & $Action | Out-Null
    }
    catch {
        $Threw = $true
    }
    Assert-True $Threw $Message
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
        throw (
            "ASSERTION FAILED: {0}. Expected <{1}> but got <{2}>." -f
            $Message,
            $Expected,
            $Actual
        )
    }
}

function Get-HeaderValues {
    param(
        [Parameter(Mandatory)]
        [psobject] $Request,

        [Parameter(Mandatory)]
        [string] $Name
    )

    return @(
        $Request.headers |
            Where-Object {
                [string]::Equals(
                    [string] $_.name,
                    $Name,
                    [StringComparison]::OrdinalIgnoreCase
                )
            } |
            ForEach-Object { [string] $_.value }
    )
}

function New-TestTaskInfo {
    param(
        [Parameter(Mandatory)]
        [string] $Status,

        [Parameter(Mandatory)]
        [string] $RunId,

        [AllowNull()]
        $Result,

        [AllowNull()]
        $ErrorValue
    )

    $Info = [ordered]@{
        description = [ordered]@{
            id = 'com.example.clone.' + $RunId.Substring(0, 8)
            default_message = 'runtime clone state ' + $Status
            args = @($RunId.Substring(8, 4))
        }
        service = 'com.vmware.vcenter.vm'
        operation = 'clone'
        status = $Status
        cancelable = ($Status -in @('PENDING', 'RUNNING', 'BLOCKED'))
    }
    if ($PSBoundParameters.ContainsKey('Result')) {
        $Info.result = $Result
    }
    if ($PSBoundParameters.ContainsKey('ErrorValue')) {
        $Info.error = $ErrorValue
    }
    return $Info
}

$FilesRoot = Split-Path -Parent $PSScriptRoot
$ContractPath = Join-Path $FilesRoot 'docs/contract.json'
$SourcesPath = Join-Path $FilesRoot 'docs/official_sources.json'
$ModuleRoot = Join-Path $FilesRoot 'VcfVcenterCloneInventory'
$ManifestPath = Join-Path $ModuleRoot 'VcfVcenterCloneInventory.psd1'
$ModulePath = Join-Path $ModuleRoot 'VcfVcenterCloneInventory.psm1'
$MockPath = Join-Path $PSScriptRoot 'mock_vcenter.py'
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'vcf91-0097-' + [guid]::NewGuid().ToString('N')
)
[IO.Directory]::CreateDirectory($TempRoot) | Out-Null
$ReadyPath = Join-Path $TempRoot 'ready.json'
$LogPath = Join-Path $TempRoot 'requests.jsonl'
$ScenarioPath = Join-Path $TempRoot 'scenario.json'
$StdoutPath = Join-Path $TempRoot 'mock.stdout'
$StderrPath = Join-Path $TempRoot 'mock.stderr'
$MockProcess = $null

$CloneOperation = 'Vcenter.VM_clone$Task'
$TaskOperation = 'Cis.Tasks_get'
$ListOperation = 'Vcenter.VM_list'
$ExpectedOperationIds = @(
    $CloneOperation,
    $TaskOperation,
    $ListOperation
)
$PinnedCommit = '3949fc33339fc5ea1b77eadb258f1cf49aa88e26'
$PinnedBlob = '8028b0824c4ff3503d05f44814f967938a795c40'
$SpecPath = 'specifications/vsphere/openapi/automation/vcenter.yaml'

try {
    $Contract = Get-Content -Raw -LiteralPath $ContractPath |
        ConvertFrom-Json -Depth 100
    $Sources = Get-Content -Raw -LiteralPath $SourcesPath |
        ConvertFrom-Json -Depth 100

    Assert-Equal $Sources.repository 'vmware/vcf-api-specs' `
        'official repository'
    Assert-Equal $Sources.repositoryCommitSha $PinnedCommit `
        'pinned VCF 9.1 repository commit'
    Assert-Equal $Sources.specPath $SpecPath `
        'official specification path'
    Assert-Equal $Sources.specBlobSha $PinnedBlob `
        'pinned specification blob'
    Assert-Equal $Sources.license 'Apache-2.0' 'official license'
    Assert-Equal (@($Sources.operationIds) -join ',') `
        ($ExpectedOperationIds -join ',') 'official operationId list'
    Assert-Equal @($Sources.operations).Count 3 `
        'official operation record count'
    foreach ($Index in 0..2) {
        Assert-Equal $Sources.operations[$Index].operationId `
            $ExpectedOperationIds[$Index] "official operation $Index identifier"
        Assert-Equal $Sources.operations[$Index].repositoryCommitSha `
            $PinnedCommit "official operation $Index records commit"
        Assert-Equal $Sources.operations[$Index].specPath `
            $SpecPath "official operation $Index records spec path"
    }

    Assert-Equal $Contract.source.commitSha $PinnedCommit `
        'contract source commit'
    Assert-Equal $Contract.source.specPath $SpecPath `
        'contract source path'
    Assert-Equal $Contract.source.specBlobSha $PinnedBlob `
        'contract source blob'
    Assert-Equal $Contract.source.openapi '3.0.3' `
        'contract OpenAPI version'
    Assert-Equal $Contract.source.apiVersion '9.1.0.0' `
        'contract API version'
    Assert-Equal $Contract.source.basePath '/api' `
        'contract base path'
    Assert-Equal $Contract.securitySchemes.api_key_auth.name `
        'vmware-api-session-id' 'contract authentication header'
    Assert-Equal @($Contract.operations).Count 3 `
        'focused contract operation count'
    Assert-Equal (($Contract.operations.operationId) -join ',') `
        ($ExpectedOperationIds -join ',') 'contract operation ordering'
    Assert-Equal (($Contract.operations.method) -join ',') `
        'POST,GET,GET' 'contract methods'
    Assert-Equal $Contract.operations[0].path '/api/vcenter/vm' `
        'clone route'
    Assert-Equal $Contract.operations[0].rawQuery `
        'action=clone&vmw-task=true' 'clone fixed query'
    Assert-True ([bool] $Contract.operations[0].requestBody.required) `
        'clone request body is required'
    Assert-True ($Contract.operations[0].responses.PSObject.Properties.Name -contains '202') `
        'clone returns HTTP 202'
    Assert-Equal $Contract.operations[1].path '/api/cis/tasks/{task}' `
        'task route'
    Assert-Equal $Contract.operations[1].rawQuery '' `
        'task read omits query'
    Assert-Equal (($Contract.operations[1].parameters.name) -join ',') `
        'task,spec' 'task parameter projection'
    Assert-True (-not [bool] $Contract.operations[1].parameters[1].required) `
        'task spec query is optional'
    Assert-Equal $Contract.operations[2].path '/api/vcenter/vm' `
        'inventory route'
    Assert-Equal (($Contract.operations[2].parameters.name) -join ',') `
        'vms,names,folders,datacenters,hosts,clusters,resource_pools,power_states' `
        'inventory filter projection'
    Assert-Equal (
        $Contract.schemas.'Cis.Task.Status'.enum -join ','
    ) 'PENDING,RUNNING,BLOCKED,SUCCEEDED,FAILED' `
        'task status projection'
    Assert-Equal (
        $Contract.schemas.'Vcenter.VM.Summary'.required -join ','
    ) 'name,power_state,vm' 'VM summary required properties'
    Assert-Equal $Contract.workflow.collectionOrdering.comparer `
        'StringComparer.Ordinal' 'contract collection comparer'

    $Manifest = Import-PowerShellDataFile -LiteralPath $ManifestPath
    Assert-Equal @($Manifest.RequiredModules).Count 1 `
        'manifest prerequisite count'
    Assert-Equal $Manifest.RequiredModules[0].ModuleName `
        'VMware.Sdk.Vcf.SddcManager' 'VCF PowerCLI module prerequisite'
    Assert-Equal ([version] $Manifest.RequiredModules[0].ModuleVersion) `
        ([version] '13.5.0.25380678') 'VCF PowerCLI module version'
    Assert-Equal (($Manifest.FunctionsToExport) -join ',') `
        'New-VcfVcenterCloneInventoryClient,Invoke-VcfVcenterCloneInventory' `
        'manifest exports'
    Assert-Equal @(
        Get-ChildItem -LiteralPath $FilesRoot -Recurse -File |
            Where-Object {
                $_.Extension -in @('.dll', '.nupkg', '.yaml', '.yml')
            }
    ).Count 0 'seed does not vendor modules, assemblies, or OpenAPI files'

    $Tokens = $null
    $ParseErrors = $null
    $Ast = [Management.Automation.Language.Parser]::ParseFile(
        $ModulePath,
        [ref] $Tokens,
        [ref] $ParseErrors
    )
    Assert-Equal @($ParseErrors).Count 0 'module parses without errors'
    $SourceText = Get-Content -Raw -LiteralPath $ModulePath
    foreach ($RequiredText in @(
        '.GetClient()',
        'Net.Http.HttpClient',
        'Net.Http.HttpRequestMessage',
        'vmware-api-session-id',
        'StringComparer]::Ordinal'
    )) {
        Assert-True (
            $SourceText.IndexOf($RequiredText, [StringComparison]::Ordinal) -ge 0
        ) "implementation must use $RequiredText"
    }
    foreach ($ForbiddenText in @(
        'Invoke-RestMethod',
        'Invoke-WebRequest',
        'Start-Process',
        'Install-Module',
        'Save-Module',
        'Update-Module',
        'Start-Job',
        'System.Net.Sockets',
        'TcpClient',
        'WebClient',
        'curl',
        'wget'
    )) {
        Assert-True (
            $SourceText.IndexOf(
                $ForbiddenText,
                [StringComparison]::OrdinalIgnoreCase
            ) -lt 0
        ) "production module must not contain $ForbiddenText"
    }

    Import-Module $ModulePath -Force -ErrorAction Stop
    $Exports = @(
        Get-Command -Module VcfVcenterCloneInventory -CommandType Function |
            Sort-Object Name |
            ForEach-Object Name
    )
    Assert-Equal ($Exports -join ',') `
        'Invoke-VcfVcenterCloneInventory,New-VcfVcenterCloneInventoryClient' `
        'runtime exports'
    $NewCommand = Get-Command New-VcfVcenterCloneInventoryClient
    $InvokeCommand = Get-Command Invoke-VcfVcenterCloneInventory
    Assert-Equal $NewCommand.Parameters.Server.ParameterType.FullName `
        'System.Uri' 'server parameter type'
    Assert-Equal $InvokeCommand.Parameters.SourceVm.ParameterType.FullName `
        'System.String' 'source VM parameter type'
    Assert-Equal $InvokeCommand.Parameters.MaxPolls.ParameterType.FullName `
        'System.Int32' 'max polls parameter type'
    Assert-Equal $InvokeCommand.Parameters.PollIntervalMilliseconds.ParameterType.FullName `
        'System.Int32' 'poll interval parameter type'

    $BorrowedHttpClient = [Net.Http.HttpClient]::new()
    $SdkConnection = [TestVcfSdkConnection]::new(
        [uri] 'https://sdk-vcenter.example.test/',
        $BorrowedHttpClient
    )
    $ConnectionClient = New-VcfVcenterCloneInventoryClient `
        -Connection $SdkConnection
    Assert-Equal $SdkConnection.GetClientCalls 1 `
        'connection form calls GetClient exactly once'
    Assert-True (
        [object]::ReferenceEquals(
            $ConnectionClient.HttpClient,
            $BorrowedHttpClient
        )
    ) 'connection form reuses the SDK HTTP client'
    Assert-Equal $ConnectionClient.BaseUri.AbsoluteUri `
        'https://sdk-vcenter.example.test/' `
        'connection form uses the SDK server URI'

    $OverrideClient = New-VcfVcenterCloneInventoryClient `
        -Connection $SdkConnection `
        -Server ([uri] 'https://override-vcenter.example.test:8443/')
    Assert-Equal $SdkConnection.GetClientCalls 2 `
        'connection form calls GetClient once per client creation'
    Assert-True (
        [object]::ReferenceEquals(
            $OverrideClient.HttpClient,
            $BorrowedHttpClient
        )
    ) 'server override still reuses the SDK HTTP client'
    Assert-Equal $OverrideClient.BaseUri.AbsoluteUri `
        'https://override-vcenter.example.test:8443/' `
        'connection form honors the server override'
    $BorrowedHttpClient.Dispose()

    Assert-Throws {
        New-VcfVcenterCloneInventoryClient -Connection ([pscustomobject]@{
            ServerUri = [uri] 'https://sdk-vcenter.example.test/'
        })
    } 'connection form rejects an object without GetClient'

    foreach ($BadServer in @(
        [uri] 'relative-vcenter',
        [uri] 'ftp://vcenter.example.test/',
        [uri] 'https://user@vcenter.example.test/',
        [uri] 'https://vcenter.example.test/sdk',
        [uri] 'https://vcenter.example.test/?query=true',
        [uri] 'https://vcenter.example.test/#fragment'
    )) {
        Assert-Throws {
            New-VcfVcenterCloneInventoryClient `
                -Server $BadServer `
                -SessionToken 'configuration-test-token'
        } "token form rejects invalid server origin $BadServer"
    }
    foreach ($UnsafeToken in @(
        '   ',
        "unsafe`rvalue",
        "unsafe`nvalue"
    )) {
        Assert-Throws {
            New-VcfVcenterCloneInventoryClient `
                -Server ([uri] 'https://vcenter.example.test/') `
                -SessionToken $UnsafeToken
        } 'token form rejects blank or header-unsafe session tokens'
    }

    $RunId = [guid]::NewGuid().ToString('N')
    $SessionToken = 'session-' + $RunId
    $SourceOne = 'vm/source one+雪-' + $RunId.Substring(0, 6)
    $NameOne = 'clone Alpha café ' + $RunId.Substring(6, 6)
    $TaskOne = 'task/one +雪-' + $RunId.Substring(12, 6) + '?'
    $SourceTwo = 'vm-source-two-' + $RunId.Substring(18, 6)
    $NameTwo = 'clone Zulu ' + $RunId.Substring(24, 6)
    $TaskTwo = 'task two/' + $RunId.Substring(0, 6) + '#'
    $FailedSource = 'vm-failed-' + $RunId.Substring(6, 6)
    $FailedName = 'failed clone ' + $RunId.Substring(12, 6)
    $FailedTask = 'task-failed/' + $RunId.Substring(18, 6)
    $LimitSource = 'vm-limit-' + $RunId.Substring(24, 6)
    $LimitName = 'limited clone ' + $RunId.Substring(0, 6)
    $LimitTask = 'task-limit/' + $RunId.Substring(6, 6)
    $UnknownSource = 'vm-unknown-' + $RunId.Substring(12, 6)
    $UnknownName = 'unknown clone ' + $RunId.Substring(18, 6)
    $UnknownTask = 'task-unknown/' + $RunId.Substring(24, 6)
    $MalformedSource = 'vm-malformed-' + $RunId.Substring(2, 6)
    $MalformedName = 'malformed clone ' + $RunId.Substring(8, 6)
    $MalformedTask = 'task-malformed/' + $RunId.Substring(14, 6)
    $SecretFailure = 'secret-error-' + $RunId

    $Inventory = @(
        [ordered]@{
            vm = 'vm-b-' + $RunId.Substring(0, 4)
            name = 'Alpha'
            power_state = 'POWERED_ON'
            cpu_count = 4
            memory_size_mib = 8192
            runtime_field = 'preserve-b'
        },
        [ordered]@{
            vm = 'vm-z-' + $RunId.Substring(4, 4)
            name = 'Zulu'
            power_state = 'POWERED_OFF'
            cpu_count = 2
            runtime_field = 'preserve-z'
        },
        [ordered]@{
            vm = 'vm-l-' + $RunId.Substring(8, 4)
            name = 'alpha'
            power_state = 'SUSPENDED'
            memory_size_mib = 4096
            runtime_field = 'preserve-l'
        },
        [ordered]@{
            vm = 'vm-a-' + $RunId.Substring(12, 4)
            name = 'Alpha'
            power_state = 'POWERED_OFF'
            runtime_field = 'preserve-a'
        }
    )
    $SuccessResultOne = [ordered]@{
        vm = 'vm-clone-' + $RunId.Substring(16, 6)
        marker = 'result-one-' + $RunId.Substring(22, 6)
    }
    $SuccessResultTwo = [ordered]@{
        vm = 'vm-clone-' + $RunId.Substring(2, 6)
        marker = 'result-two-' + $RunId.Substring(10, 6)
    }
    $MalformedTaskInfo = New-TestTaskInfo SUCCEEDED $RunId
    $MalformedTaskInfo.Remove('cancelable')
    $Scenario = [ordered]@{
        session_token = $SessionToken
        clones = @(
            [ordered]@{
                source = $SourceOne
                name = $NameOne
                task_id = $TaskOne
                polls = @(
                    (New-TestTaskInfo PENDING $RunId),
                    (New-TestTaskInfo RUNNING $RunId),
                    (New-TestTaskInfo BLOCKED $RunId),
                    (New-TestTaskInfo SUCCEEDED $RunId -Result $SuccessResultOne)
                )
            },
            [ordered]@{
                source = $SourceTwo
                name = $NameTwo
                task_id = $TaskTwo
                polls = @(
                    (New-TestTaskInfo RUNNING $RunId),
                    (New-TestTaskInfo SUCCEEDED $RunId -Result $SuccessResultTwo)
                )
            },
            [ordered]@{
                source = $FailedSource
                name = $FailedName
                task_id = $FailedTask
                polls = @(
                    (New-TestTaskInfo RUNNING $RunId),
                    (New-TestTaskInfo FAILED $RunId -ErrorValue ([ordered]@{
                        secret = $SecretFailure
                    }))
                )
            },
            [ordered]@{
                source = $LimitSource
                name = $LimitName
                task_id = $LimitTask
                polls = @(
                    (New-TestTaskInfo PENDING $RunId),
                    (New-TestTaskInfo BLOCKED $RunId),
                    (New-TestTaskInfo RUNNING $RunId),
                    (New-TestTaskInfo PENDING $RunId)
                )
            },
            [ordered]@{
                source = $UnknownSource
                name = $UnknownName
                task_id = $UnknownTask
                polls = @(
                    (New-TestTaskInfo MYSTERY $RunId)
                )
            },
            [ordered]@{
                source = $MalformedSource
                name = $MalformedName
                task_id = $MalformedTask
                polls = @($MalformedTaskInfo)
            }
        )
        inventory = $Inventory
    }
    [IO.File]::WriteAllText(
        $ScenarioPath,
        ($Scenario | ConvertTo-Json -Depth 20 -Compress),
        [Text.UTF8Encoding]::new($false)
    )

    $MockProcess = Start-Process -FilePath 'python3' -ArgumentList @(
        '-B',
        $MockPath,
        '--contract',
        $ContractPath,
        '--scenario',
        $ScenarioPath,
        '--log',
        $LogPath,
        '--ready-file',
        $ReadyPath
    ) -PassThru -RedirectStandardOutput $StdoutPath `
      -RedirectStandardError $StderrPath

    $Deadline = [Diagnostics.Stopwatch]::StartNew()
    while (-not (Test-Path -LiteralPath $ReadyPath -PathType Leaf)) {
        if ($MockProcess.HasExited) {
            $Details = Get-Content -Raw -LiteralPath $StderrPath `
                -ErrorAction SilentlyContinue
            throw "loopback mock exited before startup: $Details"
        }
        if ($Deadline.Elapsed.TotalSeconds -gt 10) {
            throw 'timed out waiting for loopback mock startup'
        }
        Start-Sleep -Milliseconds 25
    }
    $Ready = Get-Content -Raw -LiteralPath $ReadyPath | ConvertFrom-Json
    Assert-Equal $Ready.host '127.0.0.1' 'mock loopback host'
    Assert-Equal (@($Ready.operation_ids | Sort-Object) -join ',') `
        (@($ExpectedOperationIds | Sort-Object) -join ',') `
        'mock contract operation allow-list'
    $BaseUrl = 'http://127.0.0.1:' + [int] $Ready.port

    $Client = New-VcfVcenterCloneInventoryClient `
        -Server $BaseUrl `
        -SessionToken $SessionToken
    Assert-Equal @(
        Get-Content -LiteralPath $LogPath -ErrorAction SilentlyContinue |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    ).Count 0 'client creation performs no API request'

    $BeforeInvalid = @(Get-Content -LiteralPath $LogPath).Count
    $InvalidThrew = $false
    try {
        Invoke-VcfVcenterCloneInventory `
            -Client $Client `
            -SourceVm '   ' `
            -Name 'invalid' `
            -PollIntervalMilliseconds 0 | Out-Null
    }
    catch {
        $InvalidThrew = $true
    }
    Assert-True $InvalidThrew 'blank source validation fails'
    Assert-Equal @(Get-Content -LiteralPath $LogPath).Count $BeforeInvalid `
        'blank source validation occurs before traffic'

    $ResultOne = Invoke-VcfVcenterCloneInventory `
        -Client $Client `
        -SourceVm $SourceOne `
        -Name $NameOne `
        -MaxPolls 8 `
        -PollIntervalMilliseconds 0
    $ResultTwo = Invoke-VcfVcenterCloneInventory `
        -Client $Client `
        -SourceVm $SourceTwo `
        -Name $NameTwo `
        -MaxPolls 8 `
        -PollIntervalMilliseconds 0

    Assert-Equal (($ResultOne.PSObject.Properties.Name) -join ',') `
        'TaskId,Status,PollCount,Result,Inventory' 'result property order'
    Assert-Equal $ResultOne.TaskId $TaskOne 'first task identifier'
    Assert-Equal $ResultOne.Status 'SUCCEEDED' 'first terminal status'
    Assert-Equal ([int] $ResultOne.PollCount) 4 'first one-based poll count'
    Assert-Equal $ResultOne.Result.marker $SuccessResultOne.marker `
        'first task result is preserved'
    Assert-Equal $ResultTwo.TaskId $TaskTwo 'second task identifier'
    Assert-Equal $ResultTwo.Status 'SUCCEEDED' 'second terminal status'
    Assert-Equal ([int] $ResultTwo.PollCount) 2 'second one-based poll count'
    Assert-Equal $ResultTwo.Result.marker $SuccessResultTwo.marker `
        'second task result is preserved'

    $ExpectedVmOrder = @(
        $Inventory[3].vm,
        $Inventory[0].vm,
        $Inventory[1].vm,
        $Inventory[2].vm
    )
    foreach ($NumberedResult in @($ResultOne, $ResultTwo)) {
        $ActualInventory = @($NumberedResult.Inventory)
        Assert-Equal $ActualInventory.Count $Inventory.Count `
            'complete VM inventory count'
        Assert-Equal (($ActualInventory | ForEach-Object vm) -join ',') `
            ($ExpectedVmOrder -join ',') `
            'VM inventory uses ordinal name then VM ordering'
        Assert-Equal (
            ($ActualInventory | ForEach-Object runtime_field) -join ','
        ) 'preserve-a,preserve-b,preserve-z,preserve-l' `
            'complete VM summary objects are preserved'
    }
    Assert-Equal (
        (@($ResultOne.Inventory) | ForEach-Object vm) -join ','
    ) (
        (@($ResultTwo.Inventory) | ForEach-Object vm) -join ','
    ) 'opposite server orders produce the same caller-visible order'

    $FailedThrew = $false
    $FailedMessage = $null
    try {
        Invoke-VcfVcenterCloneInventory `
            -Client $Client `
            -SourceVm $FailedSource `
            -Name $FailedName `
            -MaxPolls 4 `
            -PollIntervalMilliseconds 0 | Out-Null
    }
    catch {
        $FailedThrew = $true
        $FailedMessage = [string] $_.Exception.Message
    }
    Assert-True $FailedThrew 'FAILED task terminates with an error'
    Assert-True (
        $FailedMessage.IndexOf($SessionToken, [StringComparison]::Ordinal) -lt 0
    ) 'failed-task error redacts session token'
    Assert-True (
        $FailedMessage.IndexOf($SecretFailure, [StringComparison]::Ordinal) -lt 0
    ) 'failed-task error redacts task error details'

    $LimitThrew = $false
    try {
        Invoke-VcfVcenterCloneInventory `
            -Client $Client `
            -SourceVm $LimitSource `
            -Name $LimitName `
            -MaxPolls 2 `
            -PollIntervalMilliseconds 0 | Out-Null
    }
    catch {
        $LimitThrew = $true
    }
    Assert-True $LimitThrew 'non-terminal task exhausts the exact poll limit'

    Assert-Throws {
        Invoke-VcfVcenterCloneInventory `
            -Client $Client `
            -SourceVm $UnknownSource `
            -Name $UnknownName `
            -MaxPolls 2 `
            -PollIntervalMilliseconds 0
    } 'unknown task status terminates with an error'

    Assert-Throws {
        Invoke-VcfVcenterCloneInventory `
            -Client $Client `
            -SourceVm $MalformedSource `
            -Name $MalformedName `
            -MaxPolls 2 `
            -PollIntervalMilliseconds 0
    } 'missing required task member terminates with an error before inventory'

    $LogLines = @(
        Get-Content -LiteralPath $LogPath |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    Assert-Equal $LogLines.Count 20 'exact total request count'
    $Requests = @($LogLines | ForEach-Object { $_ | ConvertFrom-Json -Depth 30 })
    $EncodedTaskOne = [uri]::EscapeDataString($TaskOne)
    $EncodedTaskTwo = [uri]::EscapeDataString($TaskTwo)
    $EncodedFailedTask = [uri]::EscapeDataString($FailedTask)
    $EncodedLimitTask = [uri]::EscapeDataString($LimitTask)
    $EncodedUnknownTask = [uri]::EscapeDataString($UnknownTask)
    $EncodedMalformedTask = [uri]::EscapeDataString($MalformedTask)
    $ExpectedOperations = @(
        $CloneOperation,
        $TaskOperation,
        $TaskOperation,
        $TaskOperation,
        $TaskOperation,
        $ListOperation,
        $CloneOperation,
        $TaskOperation,
        $TaskOperation,
        $ListOperation,
        $CloneOperation,
        $TaskOperation,
        $TaskOperation,
        $CloneOperation,
        $TaskOperation,
        $TaskOperation,
        $CloneOperation,
        $TaskOperation,
        $CloneOperation,
        $TaskOperation
    )
    $ExpectedTargets = @(
        '/api/vcenter/vm?action=clone&vmw-task=true',
        ('/api/cis/tasks/' + $EncodedTaskOne),
        ('/api/cis/tasks/' + $EncodedTaskOne),
        ('/api/cis/tasks/' + $EncodedTaskOne),
        ('/api/cis/tasks/' + $EncodedTaskOne),
        '/api/vcenter/vm',
        '/api/vcenter/vm?action=clone&vmw-task=true',
        ('/api/cis/tasks/' + $EncodedTaskTwo),
        ('/api/cis/tasks/' + $EncodedTaskTwo),
        '/api/vcenter/vm',
        '/api/vcenter/vm?action=clone&vmw-task=true',
        ('/api/cis/tasks/' + $EncodedFailedTask),
        ('/api/cis/tasks/' + $EncodedFailedTask),
        '/api/vcenter/vm?action=clone&vmw-task=true',
        ('/api/cis/tasks/' + $EncodedLimitTask),
        ('/api/cis/tasks/' + $EncodedLimitTask),
        '/api/vcenter/vm?action=clone&vmw-task=true',
        ('/api/cis/tasks/' + $EncodedUnknownTask),
        '/api/vcenter/vm?action=clone&vmw-task=true',
        ('/api/cis/tasks/' + $EncodedMalformedTask)
    )
    $ExpectedMethods = @(
        'POST', 'GET', 'GET', 'GET', 'GET', 'GET',
        'POST', 'GET', 'GET', 'GET',
        'POST', 'GET', 'GET',
        'POST', 'GET', 'GET',
        'POST', 'GET',
        'POST', 'GET'
    )
    $ExpectedCloneBodies = @(
        ([ordered]@{ source = $SourceOne; name = $NameOne } |
            ConvertTo-Json -Compress),
        ([ordered]@{ source = $SourceTwo; name = $NameTwo } |
            ConvertTo-Json -Compress),
        ([ordered]@{ source = $FailedSource; name = $FailedName } |
            ConvertTo-Json -Compress),
        ([ordered]@{ source = $LimitSource; name = $LimitName } |
            ConvertTo-Json -Compress),
        ([ordered]@{ source = $UnknownSource; name = $UnknownName } |
            ConvertTo-Json -Compress),
        ([ordered]@{ source = $MalformedSource; name = $MalformedName } |
            ConvertTo-Json -Compress)
    )
    $CloneBodyIndex = 0
    for ($Index = 0; $Index -lt $Requests.Count; $Index++) {
        $Request = $Requests[$Index]
        Assert-Equal ([int] $Request.sequence) ($Index + 1) `
            "request $Index sequence"
        Assert-Equal $Request.operation_id $ExpectedOperations[$Index] `
            "request $Index operationId"
        Assert-Equal $Request.method $ExpectedMethods[$Index] `
            "request $Index method"
        Assert-Equal $Request.target $ExpectedTargets[$Index] `
            "request $Index exact raw target"
        Assert-Equal ([int] $Request.response_status) `
            $(if ($Request.method -eq 'POST') { 202 } else { 200 }) `
            "request $Index exact response status"

        $SessionHeaders = @(Get-HeaderValues $Request 'vmware-api-session-id')
        Assert-Equal $SessionHeaders.Count 1 `
            "request $Index has exactly one session header"
        Assert-Equal $SessionHeaders[0] $SessionToken `
            "request $Index session header"
        $AcceptHeaders = @(Get-HeaderValues $Request 'Accept')
        Assert-Equal $AcceptHeaders.Count 1 `
            "request $Index has exactly one Accept header"
        Assert-True (
            $AcceptHeaders[0].IndexOf(
                'application/json',
                [StringComparison]::OrdinalIgnoreCase
            ) -ge 0
        ) "request $Index accepts JSON"
        Assert-Equal @(Get-HeaderValues $Request 'Authorization').Count 0 `
            "request $Index omits Authorization"

        if ($Request.method -eq 'POST') {
            $ContentTypes = @(Get-HeaderValues $Request 'Content-Type')
            Assert-Equal $ContentTypes.Count 1 `
                "clone request $CloneBodyIndex has one content type"
            Assert-True (
                $ContentTypes[0].StartsWith(
                    'application/json',
                    [StringComparison]::OrdinalIgnoreCase
                )
            ) "clone request $CloneBodyIndex uses JSON content"
            $ExpectedBytes = [Text.Encoding]::UTF8.GetBytes(
                $ExpectedCloneBodies[$CloneBodyIndex]
            )
            Assert-Equal $Request.body_base64 `
                ([Convert]::ToBase64String($ExpectedBytes)) `
                "clone request $CloneBodyIndex compact ordered JSON body"
            Assert-Equal ([int] $Request.body_length) $ExpectedBytes.Length `
                "clone request $CloneBodyIndex content length"
            $CloneBodyIndex++
        }
        else {
            Assert-Equal @(Get-HeaderValues $Request 'Content-Type').Count 0 `
                "GET request $Index omits content type"
            Assert-Equal ([int] $Request.body_length) 0 `
                "GET request $Index has zero-byte body"
            Assert-Equal $Request.body_base64 '' `
                "GET request $Index has empty body encoding"
        }
    }
    Assert-Equal $CloneBodyIndex 6 'all clone bodies verified'

    $ListRequests = @(
        $Requests | Where-Object { $_.operation_id -ceq $ListOperation }
    )
    Assert-Equal $ListRequests.Count 2 `
        'inventory occurs only for two successful tasks'
    Assert-Equal (($ListRequests.response_variant) -join ',') `
        'forward,reverse' 'mock flips collection order on every response'
    Assert-Equal (($ListRequests[0].response_vm_ids) -join ',') `
        (($Inventory | ForEach-Object vm) -join ',') `
        'first mock collection response is forward'
    Assert-Equal (($ListRequests[1].response_vm_ids) -join ',') `
        ((@($Inventory)[($Inventory.Count - 1)..0] | ForEach-Object vm) -join ',') `
        'second mock collection response is reversed'

    Write-Host 'ALL TESTS PASSED'
}
finally {
    if ($null -ne $MockProcess -and -not $MockProcess.HasExited) {
        Stop-Process -Id $MockProcess.Id -Force -ErrorAction SilentlyContinue
        $MockProcess.WaitForExit(5000) | Out-Null
    }
    Remove-Module VcfVcenterCloneInventory -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force `
            -ErrorAction SilentlyContinue
    }
}
