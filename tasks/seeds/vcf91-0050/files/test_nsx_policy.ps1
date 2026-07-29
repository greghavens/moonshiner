# Protected acceptance verifier for the VCF 9.1 NSX Policy rollout module.
# It uses only a loopback service and never contacts a VMware endpoint.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$PSStyle.OutputRendering = 'PlainText'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture

Set-Location -LiteralPath $PSScriptRoot
$script:Checks = 0
$script:Failures = 0

function Assert-True {
    param([string]$Label, [bool]$Condition)
    $script:Checks++
    if ($Condition) { return }
    $script:Failures++
    Write-Output "FAIL $Label"
}

function Assert-Eq {
    param([string]$Label, [AllowNull()]$Expected, [AllowNull()]$Actual)
    $script:Checks++
    if ($Expected -ceq $Actual) { return }
    $script:Failures++
    Write-Output "FAIL $Label"
    Write-Output "  expected: $Expected"
    Write-Output "  actual:   $Actual"
}

function Assert-JsonEq {
    param([string]$Label, [string]$Expected, [string]$Actual)
    $script:Checks++
    try {
        $left = [System.Text.Json.Nodes.JsonNode]::Parse($Expected)
        $right = [System.Text.Json.Nodes.JsonNode]::Parse($Actual)
        if ([System.Text.Json.Nodes.JsonNode]::DeepEquals($left, $right)) { return }
    } catch {
        # Report through the common failure path below.
    }
    $script:Failures++
    Write-Output "FAIL $Label"
    Write-Output "  expected: $Expected"
    Write-Output "  actual:   $Actual"
}

$manifestPath = Join-Path $PSScriptRoot 'VcfNsxPolicy/VcfNsxPolicy.psd1'
$modulePath = Join-Path $PSScriptRoot 'VcfNsxPolicy/VcfNsxPolicy.psm1'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $modulePath -PathType Leaf)) {
    Write-Output 'FAIL VcfNsxPolicy module files are missing'
    exit 1
}

$prerequisite = Get-Module -ListAvailable -Name VMware.Sdk.Vcf.SddcManager |
    Sort-Object Version -Descending | Select-Object -First 1
Assert-True 'VCF PowerCLI prerequisite is installed' ($null -ne $prerequisite)

$manifest = Import-PowerShellDataFile -LiteralPath $manifestPath
$requiredNames = @($manifest.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ } else { $_.ModuleName }
})
Assert-True 'manifest requires VMware.Sdk.Vcf.SddcManager' (
    $requiredNames -ccontains 'VMware.Sdk.Vcf.SddcManager'
)
Assert-Eq 'module exports exactly three functions' (
    'Invoke-VcfNsxPolicyRollout,New-VcfNsxGroupModel,New-VcfNsxSecurityPolicyModel'
) ((@($manifest.FunctionsToExport) | Sort-Object) -join ',')

$vendored = @(Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File |
    Where-Object {
        $_.Extension -in @('.dll', '.nupkg') -or
        $_.Name -match '^VMware\..*\.(psd1|psm1)$'
    })
Assert-Eq 'no VMware SDK or assembly is vendored' 0 $vendored.Count

$contractPath = Join-Path $PSScriptRoot 'docs/contract.json'
$sourcesPath = Join-Path $PSScriptRoot 'docs/official_sources.json'
$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
$sources = Get-Content -LiteralPath $sourcesPath -Raw | ConvertFrom-Json
Assert-Eq 'contract basePath' '/policy/api/v1' $contract.basePath
Assert-Eq 'contract commit matches provenance' $sources.repository_commit_sha $contract.derived_from.commit_sha
Assert-Eq 'contract spec path matches provenance' $sources.spec_path $contract.derived_from.path
$contractOps = @($contract.operations.operationId)
$sourceOps = @($sources.operations.operationId)
Assert-Eq 'contract operationIds' (
    'PatchGroupForDomain,PatchSecurityPolicyForDomain'
) ($contractOps -join ',')
Assert-Eq 'provenance operationIds' ($contractOps -join ',') ($sourceOps -join ',')
foreach ($sourceOperation in @($sources.operations)) {
    Assert-Eq "$($sourceOperation.operationId) commit" $sources.repository_commit_sha $sourceOperation.repository_commit_sha
    Assert-Eq "$($sourceOperation.operationId) spec path" $sources.spec_path $sourceOperation.spec_path
}

Import-Module -Name $manifestPath -Force
$groupModel = New-VcfNsxGroupModel -DisplayName 'payments-vms' -TagValue 'app|payments'
$policyModel = New-VcfNsxSecurityPolicyModel `
    -DisplayName 'payments-policy' `
    -SourceGroupPath '/infra/domains/default/groups/payments-vms' `
    -DestinationGroupPath '/infra/domains/default/groups/database-vms'
Assert-Eq 'group uses official generated binding type' 'VMware.Bindings.Nsx.Policy.Model.Group' $groupModel.GetType().FullName
Assert-Eq 'policy uses official generated binding type' 'VMware.Bindings.Nsx.Policy.Model.SecurityPolicy' $policyModel.GetType().FullName
Assert-Eq 'condition uses official generated binding type' 'VMware.Bindings.Nsx.Policy.Model.Condition' $groupModel.Expression[0].GetType().FullName
Assert-Eq 'rule uses official generated binding type' 'VMware.Bindings.Nsx.Policy.Model.Rule' $policyModel.Rules[0].GetType().FullName

$expectedGroup = @'
{
  "expression": [
    {
      "operator": "EQUALS",
      "member_type": "VirtualMachine",
      "key": "Tag",
      "value": "app|payments",
      "resource_type": "Condition"
    }
  ],
  "display_name": "payments-vms"
}
'@
$expectedPolicy = @'
{
  "rules": [
    {
      "action": "ALLOW",
      "direction": "IN_OUT",
      "source_groups": [
        "/infra/domains/default/groups/payments-vms"
      ],
      "services": [
        "ANY"
      ],
      "scope": [
        "ANY"
      ],
      "destination_groups": [
        "/infra/domains/default/groups/database-vms"
      ],
      "display_name": "allow-payments-vms-to-database-vms"
    }
  ],
  "sequence_number": 1200,
  "category": "Application",
  "stateful": true,
  "display_name": "payments-policy"
}
'@
Assert-JsonEq 'group builder omits every unset optional property' $expectedGroup $groupModel.ToJson()
Assert-JsonEq 'policy builder omits every unset optional property' $expectedPolicy $policyModel.ToJson()

$scratch = Join-Path $PSScriptRoot '_verify-vcf91'
$server = $null
try {
    New-Item -ItemType Directory -Force -Path $scratch > $null
    $portFile = Join-Path $scratch 'port'
    $logFile = Join-Path $scratch 'requests.jsonl'
    $server = Start-Process -FilePath 'python3' -ArgumentList @(
        (Join-Path $PSScriptRoot 'mock_nsx_policy.py'),
        $contractPath,
        $portFile,
        $logFile
    ) -PassThru -RedirectStandardOutput (Join-Path $scratch 'server.out') `
      -RedirectStandardError (Join-Path $scratch 'server.err')

    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while (-not (Test-Path -LiteralPath $portFile -PathType Leaf)) {
        if ($server.HasExited -or [DateTime]::UtcNow -gt $deadline) {
            $serverError = Get-Content -LiteralPath (Join-Path $scratch 'server.err') -Raw -ErrorAction SilentlyContinue
            throw "loopback mock failed to start: $serverError"
        }
        Start-Sleep -Milliseconds 25
    }
    $port = [int](Get-Content -LiteralPath $portFile -Raw)

    $tokens = [System.Collections.Generic.Queue[string]]::new()
    $tokens.Enqueue('access-1')
    $tokens.Enqueue('access-2')
    $tokenState = @{ Calls = 0; Queue = $tokens }
    $provider = {
        $tokenState.Calls++
        if ($tokenState.Queue.Count -eq 0) { throw 'token provider called too many times' }
        $tokenState.Queue.Dequeue()
    }.GetNewClosure()

    $result = Invoke-VcfNsxPolicyRollout `
        -BaseUri ([uri]"http://127.0.0.1:$port") `
        -DomainId 'default' `
        -GroupId 'payments-vms' `
        -TagValue 'app|payments' `
        -SecurityPolicyId 'payments-policy' `
        -DestinationGroupId 'database-vms' `
        -AccessTokenProvider $provider

    Assert-Eq 'token provider called initial plus one refresh' 2 $tokenState.Calls
    Assert-Eq 'result GroupId' 'payments-vms' $result.GroupId
    Assert-Eq 'result SecurityPolicyId' 'payments-policy' $result.SecurityPolicyId
    Assert-Eq 'result TokenRefreshes' 1 $result.TokenRefreshes
    Assert-Eq 'completed operation order' (
        'PatchGroupForDomain,PatchSecurityPolicyForDomain'
    ) (@($result.CompletedOperations) -join ',')

    $logDeadline = [DateTime]::UtcNow.AddSeconds(5)
    do {
        $lines = @(Get-Content -LiteralPath $logFile -ErrorAction SilentlyContinue |
            Where-Object { $_.Trim().Length -gt 0 })
        if ($lines.Count -ge 3) { break }
        Start-Sleep -Milliseconds 25
    } while ([DateTime]::UtcNow -lt $logDeadline)
    $entries = @($lines | ForEach-Object { $_ | ConvertFrom-Json })
    Assert-Eq 'exact request count' 3 $entries.Count

    if ($entries.Count -eq 3) {
        $expectedTargets = @(
            '/policy/api/v1/infra/domains/default/groups/payments-vms',
            '/policy/api/v1/infra/domains/default/security-policies/payments-policy',
            '/policy/api/v1/infra/domains/default/security-policies/payments-policy'
        )
        $expectedOps = @(
            'PatchGroupForDomain',
            'PatchSecurityPolicyForDomain',
            'PatchSecurityPolicyForDomain'
        )
        $expectedAuth = @('Bearer access-1', 'Bearer access-1', 'Bearer access-2')
        $expectedStatus = @(200, 401, 200)
        for ($index = 0; $index -lt 3; $index++) {
            $entry = $entries[$index]
            Assert-Eq "request $index operationId" $expectedOps[$index] $entry.operationId
            Assert-Eq "request $index method" 'PATCH' $entry.method
            Assert-Eq "request $index target" $expectedTargets[$index] $entry.target
            Assert-Eq "request $index authorization" $expectedAuth[$index] $entry.authorization
            Assert-Eq "request $index accept" 'application/json' $entry.accept
            Assert-Eq "request $index content type" 'application/json' $entry.content_type
            Assert-Eq "request $index response status" $expectedStatus[$index] $entry.status
        }
        Assert-JsonEq 'group wire body' $expectedGroup $entries[0].body
        Assert-JsonEq 'first policy wire body' $expectedPolicy $entries[1].body
        Assert-JsonEq 'policy replay body is identical' $entries[1].body $entries[2].body
    }
} catch {
    $script:Failures++
    Write-Output "FAIL verifier exception: $($_.Exception.Message)"
} finally {
    if ($null -ne $server -and -not $server.HasExited) {
        $server.Kill()
        $server.WaitForExit()
    }
    Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Module VcfNsxPolicy -Force -ErrorAction SilentlyContinue
}

if ($script:Failures -gt 0) {
    Write-Output "$($script:Failures) of $($script:Checks) checks failed"
    exit 1
}
Write-Output "all checks passed ($($script:Checks) checks)"
exit 0
