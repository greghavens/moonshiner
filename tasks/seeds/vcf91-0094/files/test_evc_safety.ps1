$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$script:Assertions = 0

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
    Assert-Equal $Actual.api_session_id 'session-test-token' "session header for $Target"
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
    Assert-True (
        @($manifest.RequiredModules.Name) -ccontains 'VMware.Sdk.Vcf.SddcManager'
    ) 'The module manifest must retain VMware.Sdk.Vcf.SddcManager as a prerequisite.'

    $vendoredVmware = @(
        Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File |
            Where-Object {
                $_.Name -like 'VMware.Sdk.Vcf*' -or
                $_.Name -like 'VMware.OpenAPI*'
            }
    )
    Assert-Equal $vendoredVmware.Count 0 'VMware SDK modules must not be vendored.'

    Import-Module $manifestPath -Force
    Assert-True ($null -ne (
        Get-Module -Name 'VMware.Sdk.Vcf.SddcManager'
    )) 'Importing VcfEvcSafety must load the installed VMware SDK prerequisite.'

    $server = Start-ContractServer -TemporaryDirectory $temporaryDirectory

    $evcMode = [pscustomobject][ordered]@{
        key = 'intel-skylake'
        masks = @(
            [pscustomobject][ordered]@{
                key = 'cpuid.avx'
                name = 'Advanced Vector Extensions'
                value = 'Val:1'
                caller_only = 'must-not-reach-wire'
            }
        )
        caller_only = 'must-not-reach-wire'
    }

    $setResult = Set-VcfClusterEvcModeSafely `
        -BaseUrl $server.BaseUrl `
        -ApiToken 'session-test-token' `
        -ClusterId 'domain-c7' `
        -EvcMode $evcMode `
        -TaskTimeoutSeconds 5 `
        -PollIntervalMilliseconds 1

    Assert-Equal $setResult.ClusterId 'domain-c7' 'set result cluster'
    Assert-Equal $setResult.Action 'Set' 'set result action'
    Assert-Equal $setResult.PrecheckTaskId 'precheck-domain-c7' 'set precheck task id'
    Assert-Equal $setResult.MutationTaskId 'mutation-domain-c7' 'set mutation task id'

    $precheckError = $null
    try {
        Set-VcfClusterEvcModeSafely `
            -BaseUrl $server.BaseUrl `
            -ApiToken 'session-test-token' `
            -ClusterId 'domain-c8' `
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
        -not $precheckError.Message.Contains('session-test-token', [StringComparison]::Ordinal)
    ) 'precheck error must not disclose the API token'

    $clearResult = Set-VcfClusterEvcModeSafely `
        -BaseUrl $server.BaseUrl `
        -ApiToken 'session-test-token' `
        -ClusterId 'domain-c10' `
        -TaskTimeoutSeconds 5 `
        -PollIntervalMilliseconds 1

    Assert-Equal $clearResult.ClusterId 'domain-c10' 'clear result cluster'
    Assert-Equal $clearResult.Action 'Clear' 'clear result action'
    Assert-Equal $clearResult.PrecheckTaskId 'precheck-domain-c10' 'clear precheck task id'
    Assert-Equal $clearResult.MutationTaskId 'mutation-domain-c10' 'clear mutation task id'

    Start-Sleep -Milliseconds 100
    $requests = @(
        Get-Content -LiteralPath $server.LogPath |
            Where-Object { $_.Trim() } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
    Assert-Equal $requests.Count 8 'exact request count across all workflows'

    $setBody = '{"evc_mode":{"key":"intel-skylake","masks":[{"key":"cpuid.avx","name":"Advanced Vector Extensions","value":"Val:1"}]}}'
    Assert-Request $requests[0] 'POST' `
        '/api/vcenter/cluster/domain-c7/evc-mode?action=check-set&vmw-task=true' `
        $setBody $true
    Assert-Request $requests[1] 'GET' `
        '/api/cis/tasks/precheck-domain-c7' '' $false
    Assert-Request $requests[2] 'PUT' `
        '/api/vcenter/cluster/domain-c7/evc-mode?vmw-task=true' `
        $setBody $true

    Assert-Request $requests[3] 'POST' `
        '/api/vcenter/cluster/domain-c8/evc-mode?action=check-set&vmw-task=true' `
        '{}' $true
    Assert-Request $requests[4] 'GET' `
        '/api/cis/tasks/precheck-domain-c8' '' $false

    Assert-Request $requests[5] 'POST' `
        '/api/vcenter/cluster/domain-c10/evc-mode?action=check-set&vmw-task=true' `
        '{}' $true
    Assert-Request $requests[6] 'GET' `
        '/api/cis/tasks/precheck-domain-c10' '' $false
    Assert-Request $requests[7] 'PUT' `
        '/api/vcenter/cluster/domain-c10/evc-mode?vmw-task=true' `
        '{}' $true

    $failedClusterMutations = @(
        $requests | Where-Object {
            $_.method -eq 'PUT' -and $_.target -like '*domain-c8*'
        }
    )
    Assert-Equal $failedClusterMutations.Count 0 `
        'the rejected precheck must gate every mutation for domain-c8'

    foreach ($request in $requests) {
        Assert-True (
            $request.target -notmatch '\?spec(?:=|&|$)'
        ) 'unset Cis.Tasks_get spec query must be omitted'
        Assert-True (
            $request.body -notmatch '"evc_mode":(?:null|""|\{\})'
        ) 'unset evc_mode must be omitted, never serialized empty'
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
