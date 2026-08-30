# Protected verifier for VksSupervisorAuth.
# Runs entirely against mock_vcenter.py on 127.0.0.1.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$PSStyle.OutputRendering = 'PlainText'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
Set-Location -LiteralPath $PSScriptRoot

$script:Checks = 0
$script:Fails = 0
$server = $null
$worker = $null
$workerAsync = $null

function Assert-True {
    param([string]$Label, [bool]$Condition)
    $script:Checks++
    if ($Condition) { return }
    $script:Fails++
    Write-Output "FAIL $Label"
}

function Assert-Eq {
    param([string]$Label, $Expected, $Actual)
    $script:Checks++
    if ("$Expected" -ceq "$Actual") { return }
    $script:Fails++
    Write-Output "FAIL $Label"
    Write-Output "  expected: $Expected"
    Write-Output "  actual:   $Actual"
}

function Wait-Until {
    param(
        [Parameter(Mandatory)][scriptblock]$Condition,
        [Parameter(Mandatory)][string]$Failure,
        [int]$Seconds = 20
    )
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    while (-not (& $Condition)) {
        if ([DateTime]::UtcNow -gt $deadline) { throw $Failure }
        Start-Sleep -Milliseconds 25
    }
}

$T = Join-Path $PSScriptRoot '_verify'
$portFile = Join-Path $T 'port.txt'
$logFile = Join-Path $T 'requests.jsonl'
$gateDir = Join-Path $T 'gates'
$moduleManifest = Join-Path $PSScriptRoot 'VksSupervisorAuth.psd1'
$moduleFile = Join-Path $PSScriptRoot 'VksSupervisorAuth.psm1'
$oldUser = 'svc-vks'
$oldPassword = 'dummy-old-41f6'
$newUser = 'svc-vks'
$newPassword = 'dummy-new-92ab'
$oldSession = 'session-old-1f6d4a'
$newSession = 'session-new-8c0e27'
$supervisor = 'domain-c8:supervisor-7ca91'

function Get-RequestLog {
    if (-not (Test-Path -LiteralPath $logFile -PathType Leaf)) { return @() }
    $records = [System.Collections.Generic.List[object]]::new()
    foreach ($line in @(Get-Content -LiteralPath $logFile -ErrorAction Stop)) {
        if (-not [string]::IsNullOrWhiteSpace($line)) {
            $records.Add(($line | ConvertFrom-Json))
        }
    }
    return $records.ToArray()
}

function New-DummyCredential {
    param([string]$Username, [string]$Password)
    [pscredential]::new(
        $Username,
        (ConvertTo-SecureString -String $Password -AsPlainText -Force)
    )
}

try {
    if (Test-Path -LiteralPath $T) {
        Remove-Item -LiteralPath $T -Recurse -Force
    }
    New-Item -ItemType Directory -Path $gateDir -Force > $null

    # Protected provenance and contract must name exactly the used spec operations.
    $contract = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'docs/contract.json') -Raw |
        ConvertFrom-Json
    $sources = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'docs/official_sources.json') -Raw |
        ConvertFrom-Json
    $expectedSha = 'c3f3b52c845dd967cabbc21680e893292077d5ba'
    $expectedSpecPath = 'specifications/vsphere/openapi/automation/vcenter.yaml'
    $expectedOperations = @(
        'Cis.Session_create',
        'Cis.Session_delete',
        'Vcenter.Namespaces.Instances_listV2',
        'Vcenter.NamespaceManagement.Supervisors.Workloads.KubeApiServerSettings_get',
        'Vcenter.NamespaceManagement.Supervisors.Workloads.KubeApiServerSettings_update'
    )
    Assert-Eq 'contract pins vSphere API 9.1.0.0' '9.1.0.0' $contract.derived_from.api_version
    Assert-Eq 'contract pins repository commit' $expectedSha $contract.derived_from.repository_commit_sha
    Assert-Eq 'contract pins exact specification path' $expectedSpecPath $contract.derived_from.spec_path
    Assert-Eq 'official sources pins repository commit' $expectedSha $sources.repository_commit_sha
    Assert-Eq 'official sources pins exact specification path' $expectedSpecPath $sources.spec_path
    Assert-Eq 'official source operationIds are exact' ($expectedOperations -join ',') `
        ((@($sources.operations | ForEach-Object { $_.operationId })) -join ',')
    Assert-Eq 'contract operationIds are exact' (($expectedOperations | Sort-Object) -join ',') `
        ((@($contract.operations.PSObject.Properties.Name) | Sort-Object) -join ',')
    foreach ($operation in @($sources.operations)) {
        Assert-Eq "$($operation.operationId) repeats spec path" $expectedSpecPath $operation.spec_path
        Assert-Eq "$($operation.operationId) repeats commit" $expectedSha $operation.repository_commit_sha
    }

    Assert-True 'VksSupervisorAuth.psd1 exists' (Test-Path -LiteralPath $moduleManifest -PathType Leaf)
    Assert-True 'VksSupervisorAuth.psm1 exists' (Test-Path -LiteralPath $moduleFile -PathType Leaf)
    Assert-True 'VMware.Sdk.Vcf prerequisite is installed' `
        ($null -ne (Get-Module -ListAvailable -Name 'VMware.Sdk.Vcf.SddcManager' | Select-Object -First 1))
    $manifest = Test-ModuleManifest -Path $moduleManifest
    Assert-True 'manifest requires VMware.Sdk.Vcf.SddcManager' `
        (@($manifest.RequiredModules | ForEach-Object { $_.Name }) -contains 'VMware.Sdk.Vcf.SddcManager')
    $expectedExports = @(
        'Connect-VksSupervisor',
        'Disconnect-VksSupervisor',
        'Get-VksKubeApiServerSettings',
        'Get-VksSupervisorNamespaces',
        'New-VksVcfCredentialUpdateSpec',
        'Set-VksKubeApiServerSettings',
        'Update-VksSupervisorCredential'
    )
    Assert-Eq 'manifest exports exactly the requested functions' ($expectedExports -join ',') `
        ((@($manifest.ExportedFunctions.Keys) | Sort-Object) -join ',')

    $server = Start-Process -FilePath 'python3' -ArgumentList @(
        (Join-Path $PSScriptRoot 'mock_vcenter.py'),
        $portFile,
        $logFile,
        $gateDir
    ) -PassThru -RedirectStandardOutput (Join-Path $T 'server.out') `
        -RedirectStandardError (Join-Path $T 'server.err')
    Wait-Until -Condition { Test-Path -LiteralPath $portFile -PathType Leaf } `
        -Failure 'loopback contract mock did not publish its port'
    $port = [int](Get-Content -LiteralPath $portFile -Raw).Trim()
    $baseUrl = "http://127.0.0.1:$port"

    Import-Module $moduleManifest -Force

    # Real VMware.Sdk.Vcf model initializers must drive the credential update plan.
    $plan = New-VksVcfCredentialUpdateSpec -ResourceName 'vcsa-lab-01' `
        -ResourceId 'vc-0d71' -Username $newUser `
        -NewPassword (ConvertTo-SecureString -String $newPassword -AsPlainText -Force)
    Assert-Eq 'returns SDK CredentialsUpdateSpec' `
        'VMware.Bindings.Vcf.SddcManager.Model.CredentialsUpdateSpec' $plan.GetType().FullName
    Assert-Eq 'SDK plan operation is UPDATE' 'UPDATE' $plan.OperationType
    Assert-Eq 'SDK plan contains one vCenter resource' 1 @($plan.Elements).Count
    Assert-Eq 'SDK resource type is VCENTER' 'VCENTER' $plan.Elements[0].ResourceType
    Assert-Eq 'SDK resource name is preserved' 'vcsa-lab-01' $plan.Elements[0].ResourceName
    Assert-Eq 'SDK resource id is preserved' 'vc-0d71' $plan.Elements[0].ResourceId
    Assert-Eq 'SDK API credential username is preserved' $newUser `
        $plan.Elements[0].Credentials[0].Username
    Assert-Eq 'SDK credential type is API' 'API' $plan.Elements[0].Credentials[0].CredentialType
    Assert-Eq 'SDK account type is SERVICE' 'SERVICE' $plan.Elements[0].Credentials[0].AccountType
    Assert-True 'unset SDK auto-rotate policy remains omitted' ($null -eq $plan.AutoRotatePolicy)

    # Initial Cis.Session_create uses Basic auth and no request body.
    $oldCredential = New-DummyCredential -Username $oldUser -Password $oldPassword
    $invalidBaseUrlError = $null
    try {
        Connect-VksSupervisor -BaseUrl "$baseUrl/not-an-authority" `
            -Credential $oldCredential > $null
    } catch {
        $invalidBaseUrlError = $_
    }
    Assert-True 'BaseUrl containing a path is rejected' ($null -ne $invalidBaseUrlError)
    Assert-Eq 'invalid BaseUrl is rejected before any request is sent' 0 @(Get-RequestLog).Count

    $badUser = 'svc-vks-invalid'
    $badPassword = 'dummy-bad-credential-7d2a'
    $badCredential = New-DummyCredential -Username $badUser -Password $badPassword
    $badLoginError = $null
    try {
        Connect-VksSupervisor -BaseUrl $baseUrl -Credential $badCredential > $null
    } catch {
        $badLoginError = $_
    }
    Assert-True 'rejected login reports HTTP 401' `
        ("$badLoginError" -like '*HTTP 401*')
    Assert-True 'rejected login does not expose its username' `
        ("$badLoginError" -notlike "*$badUser*")
    Assert-True 'rejected login does not expose its password' `
        ("$badLoginError" -notlike "*$badPassword*")

    $client = Connect-VksSupervisor -BaseUrl ($baseUrl + '/') -Credential $oldCredential
    Assert-True 'client does not expose Credential' `
        (-not (@($client.PSObject.Properties.Name) -contains 'Credential'))
    Assert-True 'client does not expose Password' `
        (-not (@($client.PSObject.Properties.Name) -contains 'Password'))
    Assert-True 'client retains no PSCredential property value' `
        (-not (@($client.PSObject.Properties.Value | Where-Object { $_ -is [pscredential] }).Count))
    Assert-True 'client retains no plaintext password property value' `
        (-not (@($client.PSObject.Properties.Value | Where-Object { "$_" -ceq $oldPassword }).Count))

    # Begin a namespace request under the old session and wait until the mock holds it.
    $worker = [powershell]::Create()
    $null = $worker.AddScript({
        param($ManifestPath, $SharedClient)
        Import-Module -Name $ManifestPath -Force
        @(Get-VksSupervisorNamespaces -Client $SharedClient)
    }).AddArgument($moduleManifest).AddArgument($client)
    $workerAsync = $worker.BeginInvoke()
    $oldReceived = Join-Path $gateDir 'old_received'
    Wait-Until -Condition { Test-Path -LiteralPath $oldReceived -PathType Leaf } `
        -Failure 'old-session namespace request did not reach the mock'

    # Authenticate the replacement, atomically publish it, and make new API calls.
    $newCredential = New-DummyCredential -Username $newUser -Password $newPassword
    Update-VksSupervisorCredential -Client $client -Credential $newCredential
    $settings = Get-VksKubeApiServerSettings -Client $client -Supervisor $supervisor
    Assert-Eq 'settings certificate DNS name' 'api.platform.example.test' `
        (@($settings.certificate_dns_names) -join ',')
    Assert-True 'settings fairness is true' ([bool]$settings.namespace_api_fairness_enabled)

    $emptyUpdateError = $null
    try {
        Set-VksKubeApiServerSettings -Client $client -Supervisor $supervisor
    } catch {
        $emptyUpdateError = $_
    }
    Assert-True 'an update with no bound optional fields is rejected locally' `
        ($null -ne $emptyUpdateError)

    Set-VksKubeApiServerSettings -Client $client -Supervisor $supervisor `
        -CertificateDnsNamesToAdd @('api.blue.example.test', 'api.green.example.test')
    Set-VksKubeApiServerSettings -Client $client -Supervisor $supervisor `
        -NamespaceApiFairnessEnabled $false
    Set-VksKubeApiServerSettings -Client $client -Supervisor $supervisor `
        -CertificateDnsNamesToRemove @('api.legacy-a.example.test', 'api.legacy-b.example.test')

    # The old session must still be alive while its namespace request is held.
    $beforeRelease = @(Get-RequestLog)
    $oldDeletesBeforeRelease = @($beforeRelease | Where-Object {
        $_.method -ceq 'DELETE' -and $_.path -ceq '/api/session' -and
        $_.session -ceq $oldSession
    })
    Assert-Eq 'old session is not deleted while request is in flight' 0 $oldDeletesBeforeRelease.Count

    Set-Content -LiteralPath (Join-Path $gateDir 'release_old') -Value 'release'
    $namespaceOutput = @($worker.EndInvoke($workerAsync))
    $workerAsync = $null
    if ($worker.Streams.Error.Count -gt 0) {
        throw "namespace worker failed: $($worker.Streams.Error[0])"
    }
    Assert-Eq 'v2 namespace list preserves two results' 2 $namespaceOutput.Count
    Assert-Eq 'first namespace identifier is preserved' 'payments-dev' $namespaceOutput[0].namespace
    Assert-Eq 'second namespace identifier is preserved' 'orders-prod' $namespaceOutput[1].namespace

    Wait-Until -Condition {
        @((Get-RequestLog) | Where-Object {
            $_.method -ceq 'DELETE' -and $_.path -ceq '/api/session' -and
            $_.session -ceq $oldSession
        }).Count -eq 1
    } -Failure 'retired old session was not deleted after its request drained'

    Disconnect-VksSupervisor -Client $client
    $log = @(Get-RequestLog)
    foreach ($request in $log) {
        Assert-Eq 'every selected operation asks for JSON responses' `
            'application/json' $request.accept
    }

    # Exact Cis.Session_create wire shape.
    $sessionPosts = @($log | Where-Object {
        $_.method -ceq 'POST' -and $_.path -ceq '/api/session'
    })
    Assert-Eq 'exactly three session creates, including the rejected login' 3 $sessionPosts.Count
    $badBasic = 'Basic ' + [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes("$badUser`:$badPassword")
    )
    $oldBasic = 'Basic ' + [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes("$oldUser`:$oldPassword")
    )
    $newBasic = 'Basic ' + [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes("$newUser`:$newPassword")
    )
    Assert-Eq 'rejected login exact Basic header' $badBasic $sessionPosts[0].authorization
    Assert-Eq 'initial login exact Basic header' $oldBasic $sessionPosts[1].authorization
    Assert-Eq 'replacement login exact Basic header' $newBasic $sessionPosts[2].authorization
    foreach ($sessionPost in $sessionPosts) {
        Assert-Eq 'session create has no API session header' '' "$($sessionPost.session)"
        Assert-Eq 'session create has no request body' '' $sessionPost.body
        Assert-Eq 'session create has no content type' '' "$($sessionPost.content_type)"
        Assert-Eq 'session create has no query' '' $sessionPost.query
        Assert-Eq 'session create asks for JSON' 'application/json' $sessionPost.accept
    }

    # The held request used old; every operation started after publication used new.
    $namespaceGets = @($log | Where-Object {
        $_.method -ceq 'GET' -and $_.path -ceq '/api/vcenter/namespaces/instances/v2'
    })
    Assert-Eq 'namespace list sent exactly once (no replay)' 1 $namespaceGets.Count
    Assert-Eq 'in-flight namespace request kept old session' $oldSession $namespaceGets[0].session
    Assert-Eq 'namespace list has no query' '' $namespaceGets[0].query
    Assert-Eq 'namespace list has no body' '' $namespaceGets[0].body
    Assert-Eq 'namespace list has no content type' '' "$($namespaceGets[0].content_type)"
    Assert-Eq 'namespace list has no Basic authorization' '' "$($namespaceGets[0].authorization)"

    $encodedSupervisor = [uri]::EscapeDataString($supervisor)
    $settingsPath = "/api/vcenter/namespace-management/supervisors/$encodedSupervisor/workloads/kube-api-server-settings"
    $settingsGets = @($log | Where-Object {
        $_.method -ceq 'GET' -and $_.path -ceq $settingsPath
    })
    Assert-Eq 'settings GET exact path and count' 1 $settingsGets.Count
    Assert-Eq 'settings GET uses replacement session' $newSession $settingsGets[0].session
    Assert-Eq 'settings GET has no query' '' $settingsGets[0].query
    Assert-Eq 'settings GET has no body' '' $settingsGets[0].body
    Assert-Eq 'settings GET has no content type' '' "$($settingsGets[0].content_type)"
    Assert-Eq 'settings GET has no Basic authorization' '' "$($settingsGets[0].authorization)"

    $patches = @($log | Where-Object {
        $_.method -ceq 'PATCH' -and $_.path -ceq $settingsPath
    })
    Assert-Eq 'exactly three settings PATCHes' 3 $patches.Count
    Assert-Eq 'DNS add exact JSON and caller order' `
        '{"certificate_dns_names_to_add_list":["api.blue.example.test","api.green.example.test"]}' `
        $patches[0].body
    Assert-Eq 'false boolean exact JSON' '{"namespace_api_fairness_enabled":false}' $patches[1].body
    Assert-Eq 'DNS remove exact JSON and caller order' `
        '{"certificate_dns_names_to_remove_list":["api.legacy-a.example.test","api.legacy-b.example.test"]}' `
        $patches[2].body
    foreach ($patch in $patches) {
        Assert-Eq 'PATCH uses replacement session' $newSession $patch.session
        Assert-Eq 'PATCH has no query' '' $patch.query
        Assert-Eq 'PATCH has no Basic authorization' '' "$($patch.authorization)"
        Assert-True 'PATCH content type is application/json' `
            ("$($patch.content_type)" -like 'application/json*')
        $bodyObject = $patch.body | ConvertFrom-Json
        Assert-True 'unset add-list omitted unless explicitly bound' `
            ((@($bodyObject.PSObject.Properties.Name) -contains 'certificate_dns_names_to_add_list') -eq
                ($patch.sequence -eq $patches[0].sequence))
        Assert-True 'unset remove-list omitted unless explicitly bound' `
            ((@($bodyObject.PSObject.Properties.Name) -contains 'certificate_dns_names_to_remove_list') -eq
                ($patch.sequence -eq $patches[2].sequence))
        Assert-True 'unset fairness omitted unless explicitly bound' `
            ((@($bodyObject.PSObject.Properties.Name) -contains 'namespace_api_fairness_enabled') -eq
                ($patch.sequence -eq $patches[1].sequence))
    }

    # Old logout follows the old request's completion; disconnect logs out new.
    $deletes = @($log | Where-Object {
        $_.method -ceq 'DELETE' -and $_.path -ceq '/api/session'
    })
    Assert-Eq 'exactly one logout per session' 2 $deletes.Count
    Assert-Eq 'retired session logged out first' $oldSession $deletes[0].session
    Assert-Eq 'current session logged out on disconnect' $newSession $deletes[1].session
    foreach ($delete in $deletes) {
        Assert-Eq 'logout has no Basic authorization' '' "$($delete.authorization)"
        Assert-Eq 'logout has no request body' '' $delete.body
        Assert-Eq 'logout has no content type' '' "$($delete.content_type)"
        Assert-Eq 'logout has no query' '' $delete.query
    }
    Assert-True 'old logout occurs after replacement settings requests' `
        ([int]$deletes[0].sequence -gt [int]$patches[2].sequence)

    $knownShapes = @(
        'POST /api/session',
        'DELETE /api/session',
        'GET /api/vcenter/namespaces/instances/v2',
        "GET $settingsPath",
        "PATCH $settingsPath"
    )
    $actualShapes = @($log | ForEach-Object { "$($_.method) $($_.path)" } | Sort-Object -Unique)
    Assert-Eq 'mock observed only contract-named operation shapes' `
        (($knownShapes | Sort-Object) -join ',') ($actualShapes -join ',')
} catch {
    $script:Fails++
    Write-Output "FAIL verifier exception: $($_.Exception.Message)"
    if (Test-Path -LiteralPath (Join-Path $T 'server.err')) {
        $serverError = Get-Content -LiteralPath (Join-Path $T 'server.err') -Raw
        if (-not [string]::IsNullOrWhiteSpace($serverError)) {
            Write-Output "mock stderr: $serverError"
        }
    }
} finally {
    if ($null -ne $workerAsync -and $null -ne $worker) {
        try { $worker.Stop() } catch {}
    }
    if ($null -ne $worker) { $worker.Dispose() }
    if ($null -ne $server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
}

Write-Output "checks=$($script:Checks) fails=$($script:Fails)"
if ($script:Fails -gt 0) { exit 1 }
Write-Output 'ALL TESTS PASSED'
exit 0
