$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$script:Assertions = 0
$script:SessionId = '0123456789abcdef0123456789abcdef'
$script:ServiceUuid = '7978ee81-a66c-4c37-8653-c577c0161e9d'

function Assert-True {
    param(
        [Parameter(Mandatory)]
        [bool] $Condition,

        [Parameter(Mandatory)]
        [string] $Message
    )

    $script:Assertions++
    if (-not $Condition) {
        throw "ASSERTION FAILED: $Message"
    }
}

function Assert-Equal {
    param(
        [AllowNull()]
        [object] $Actual,

        [AllowNull()]
        [object] $Expected,

        [Parameter(Mandatory)]
        [string] $Message
    )

    $script:Assertions++
    if ($Actual -cne $Expected) {
        throw "ASSERTION FAILED: $Message`nExpected: <$Expected>`nActual:   <$Actual>"
    }
}

function Start-ContractServer {
    param(
        [Parameter(Mandatory)]
        [string] $TemporaryDirectory
    )

    $python = (Get-Command python3 -ErrorAction Stop).Source
    $info = [System.Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $python
    $info.WorkingDirectory = $PSScriptRoot
    $info.UseShellExecute = $false
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($argument in @(
        (Join-Path $PSScriptRoot 'mock_vcenter.py'),
        '--contract',
        (Join-Path $PSScriptRoot 'docs/contract.json'),
        '--log-file',
        (Join-Path $TemporaryDirectory 'requests.jsonl'),
        '--port-file',
        (Join-Path $TemporaryDirectory 'port')
    )) {
        [void] $info.ArgumentList.Add($argument)
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $info
    [void] $process.Start()

    $portFile = Join-Path $TemporaryDirectory 'port'
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path -LiteralPath $portFile)) {
        if ($process.HasExited) {
            $stderr = $process.StandardError.ReadToEnd()
            throw "mock_vcenter.py exited before startup: $stderr"
        }
        if ([DateTime]::UtcNow -ge $deadline) {
            $process.Kill()
            throw 'Timed out waiting for mock_vcenter.py.'
        }
        Start-Sleep -Milliseconds 25
    }

    return [pscustomobject]@{
        Process = $process
        BaseUrl = "http://127.0.0.1:$(
            (Get-Content -LiteralPath $portFile -Raw).Trim()
        )"
        LogPath = Join-Path $TemporaryDirectory 'requests.jsonl'
    }
}

function Assert-Request {
    param(
        [Parameter(Mandatory)]
        [object] $Actual,

        [Parameter(Mandatory)]
        [string] $Method,

        [Parameter(Mandatory)]
        [string] $Target,

        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string] $Body,

        [Parameter(Mandatory)]
        [bool] $HasJsonBody
    )

    Assert-Equal $Actual.method $Method "HTTP method for $Target"
    Assert-Equal $Actual.target $Target "request target for $Target"
    Assert-Equal $Actual.body $Body "raw request body for $Target"
    Assert-Equal $Actual.api_session_id $script:SessionId "session header for $Target"
    if ($HasJsonBody) {
        Assert-True (
            [string]$Actual.content_type
        ).StartsWith('application/json', [StringComparison]::OrdinalIgnoreCase) `
            "Content-Type for $Target"
    }
    else {
        Assert-True ([string]::IsNullOrEmpty([string]$Actual.content_type)) `
            "GET $Target must not carry a content type"
    }
}

$temporaryDirectory = Join-Path (
    [System.IO.Path]::GetTempPath()
) "vcf91-0094-$([Guid]::NewGuid().ToString('N'))"
[void] (New-Item -ItemType Directory -Path $temporaryDirectory)
$server = $null

try {
    $manifestPath = Join-Path $PSScriptRoot 'VcfEvcSafety.psd1'
    $manifest = Test-ModuleManifest -Path $manifestPath
    Assert-Equal @($manifest.RequiredModules).Count 0 `
        'The direct HTTP module must not declare an unrelated SDK prerequisite.'

    $vendoredVmware = @(
        Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File |
            Where-Object {
                $_.Name -like 'VMware.Sdk.Vcf*' -or
                $_.Name -like 'VMware.OpenAPI*'
            }
    )
    Assert-Equal $vendoredVmware.Count 0 'VMware SDK modules must not be vendored.'

    $moduleText = Get-Content -Raw -LiteralPath (
        Join-Path $PSScriptRoot 'VcfEvcSafety.psm1'
    )
    Assert-True $moduleText.Contains(
        'DangerousAcceptAnyServerCertificateValidator',
        [StringComparison]::Ordinal
    ) 'Certificate skipping must use the static .NET validator.'
    Assert-True (-not $moduleText.Contains(
        'VMware.Sdk.Vcf.SddcManager',
        [StringComparison]::Ordinal
    )) 'The implementation must not require the unrelated SDDC Manager module.'

    Import-Module $manifestPath -Force
    $evcParameter = (Get-Command Set-VcfClusterEvcModeSafely).`
        Parameters['EvcMode']
    Assert-True ([bool] @($evcParameter.Attributes).Where({
        $_ -is [Management.Automation.ParameterAttribute] -and $_.Mandatory
    }).Count) `
        'EvcMode must be mandatory; unsupported clear-by-omission is removed.'

    $server = Start-ContractServer -TemporaryDirectory $temporaryDirectory

    $supportedMasks = @(
        [pscustomobject][ordered]@{ key = 'cpuid.CMPXCHG16B'; name = 'cpuid.CMPXCHG16B'; value = 'Val:1' },
        [pscustomobject][ordered]@{ key = 'cpuid.DS'; name = 'cpuid.DS'; value = 'Val:1' },
        [pscustomobject][ordered]@{ key = 'cpuid.FAMILY'; name = 'cpuid.FAMILY'; value = 'Val:6' },
        [pscustomobject][ordered]@{ key = 'cpuid.Intel'; name = 'cpuid.Intel'; value = 'Val:1' },
        [pscustomobject][ordered]@{ key = 'cpuid.LAHF64'; name = 'cpuid.LAHF64'; value = 'Val:1' },
        [pscustomobject][ordered]@{ key = 'cpuid.LM'; name = 'cpuid.LM'; value = 'Val:1' },
        [pscustomobject][ordered]@{ key = 'cpuid.MODEL'; name = 'cpuid.MODEL'; value = 'Val:0xf' },
        [pscustomobject][ordered]@{ key = 'cpuid.MWAIT'; name = 'cpuid.MWAIT'; value = 'Val:1' },
        [pscustomobject][ordered]@{ key = 'cpuid.NUM_EXT_LEVELS'; name = 'cpuid.NUM_EXT_LEVELS'; value = 'Val:0x80000008' },
        [pscustomobject][ordered]@{ key = 'cpuid.NUMLEVELS'; name = 'cpuid.NUMLEVELS'; value = 'Val:0xa' },
        [pscustomobject][ordered]@{ key = 'cpuid.NX'; name = 'cpuid.NX'; value = 'Val:1' },
        [pscustomobject][ordered]@{ key = 'cpuid.SS'; name = 'cpuid.SS'; value = 'Val:1' },
        [pscustomobject][ordered]@{ key = 'cpuid.SSE3'; name = 'cpuid.SSE3'; value = 'Val:1' },
        [pscustomobject][ordered]@{ key = 'cpuid.SSSE3'; name = 'cpuid.SSSE3'; value = 'Val:1' },
        [pscustomobject][ordered]@{ key = 'cpuid.STEPPING'; name = 'cpuid.STEPPING'; value = 'Val:1' }
    )
    $evcMode = [pscustomobject][ordered]@{
        key = 'intel-merom'
        masks = @(
            $supportedMasks | ForEach-Object {
                [pscustomobject][ordered]@{
                    key = $_.key
                    name = $_.name
                    value = $_.value
                    caller_only = 'must-not-reach-wire'
                }
            }
        )
        caller_only = 'must-not-reach-wire'
    }
    $setBody = [ordered]@{
        evc_mode = [ordered]@{
            key = 'intel-merom'
            masks = $supportedMasks
        }
    } | ConvertTo-Json -Depth 8 -Compress

    $setResult = Set-VcfClusterEvcModeSafely `
        -BaseUrl $server.BaseUrl `
        -ApiToken $script:SessionId `
        -ClusterId 'domain-c9' `
        -EvcMode $evcMode `
        -TaskTimeoutSeconds 5 `
        -PollIntervalMilliseconds 1

    Assert-Equal $setResult.ClusterId 'domain-c9' 'set result cluster'
    Assert-Equal $setResult.Action 'Set' 'set result action'
    Assert-Equal $setResult.PrecheckTaskId `
        "task-4125:$script:ServiceUuid" 'set precheck task id'
    Assert-Equal $setResult.MutationTaskId `
        "task-4128:$script:ServiceUuid" 'set mutation task id'

    $precheckError = $null
    try {
        Set-VcfClusterEvcModeSafely `
            -BaseUrl $server.BaseUrl `
            -ApiToken $script:SessionId `
            -ClusterId 'domain-c8' `
            -EvcMode $evcMode `
            -TaskTimeoutSeconds 5 `
            -PollIntervalMilliseconds 1
    }
    catch {
        $precheckError = $_.Exception
    }
    Assert-True ($null -ne $precheckError) 'a non-empty precheck result must throw'
    Assert-Equal (
        $precheckError.GetType().Name
    ) 'VcfEvcPrecheckException' 'precheck failure exception type'
    Assert-True (
        -not $precheckError.Message.Contains($script:SessionId, [StringComparison]::Ordinal)
    ) 'precheck error must not disclose the API token'

    $resultError = $null
    try {
        Set-VcfClusterEvcModeSafely `
            -BaseUrl $server.BaseUrl `
            -ApiToken $script:SessionId `
            -ClusterId 'domain-c10' `
            -EvcMode $evcMode `
            -TaskTimeoutSeconds 5 `
            -PollIntervalMilliseconds 1
    }
    catch {
        $resultError = $_.Exception
    }
    Assert-True ($null -ne $resultError) `
        'a non-empty successful precheck result must throw'
    Assert-Equal $resultError.GetType().Name `
        'VcfEvcPrecheckException' 'check-result failure exception type'

    Start-Sleep -Milliseconds 100
    $requests = @(
        Get-Content -LiteralPath $server.LogPath |
            Where-Object { $_.Trim() } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
    Assert-Equal $requests.Count 7 'exact request count across all workflows'
    Assert-Request $requests[0] 'POST' `
        '/api/vcenter/cluster/domain-c9/evc-mode?action=check-set&vmw-task=true' `
        $setBody $true
    Assert-Request $requests[1] 'GET' `
        "/api/cis/tasks/$([uri]::EscapeDataString("task-4125:$script:ServiceUuid"))" '' $false
    Assert-Request $requests[2] 'PUT' `
        '/api/vcenter/cluster/domain-c9/evc-mode?vmw-task=true' `
        $setBody $true

    Assert-Request $requests[3] 'POST' `
        '/api/vcenter/cluster/domain-c8/evc-mode?action=check-set&vmw-task=true' `
        $setBody $true
    Assert-Request $requests[4] 'GET' `
        "/api/cis/tasks/$([uri]::EscapeDataString("task-4126:$script:ServiceUuid"))" '' $false

    Assert-Request $requests[5] 'POST' `
        '/api/vcenter/cluster/domain-c10/evc-mode?action=check-set&vmw-task=true' `
        $setBody $true
    Assert-Request $requests[6] 'GET' `
        "/api/cis/tasks/$([uri]::EscapeDataString("task-4127:$script:ServiceUuid"))" '' $false

    $failedClusterMutations = @(
        $requests | Where-Object {
            $_.method -eq 'PUT' -and (
                $_.target -like '*domain-c8*' -or
                $_.target -like '*domain-c10*'
            )
        }
    )
    Assert-Equal $failedClusterMutations.Count 0 `
        'every rejected precheck must gate its mutation'

    foreach ($request in $requests) {
        Assert-True (
            $request.target -notmatch '\?spec(?:=|&|$)'
        ) 'unset Cis.Tasks_get spec query must be omitted'
        Assert-True (
            $request.body -notmatch '"evc_mode":(?:null|""|\{\})'
        ) 'evc_mode must never be serialized null or empty'
        Assert-True (
            $request.body -notmatch 'caller_only'
        ) 'caller-added properties must not reach the wire'
    }

    Write-Host "ALL TESTS PASSED ($script:Assertions assertions)"
}
finally {
    Remove-Module VcfEvcSafety -Force -ErrorAction SilentlyContinue
    if ($null -ne $server -and -not $server.Process.HasExited) {
        $server.Process.Kill()
        $server.Process.WaitForExit(5000)
    }
    if (Test-Path -LiteralPath $temporaryDirectory) {
        Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
    }
}
