# Protected verifier for the VCF 9.1 retry-safe NSX IP block module.
# It binds a contract-pinned mock to loopback and never contacts VMware.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$PSStyle.OutputRendering = 'PlainText'
[System.Globalization.CultureInfo]::CurrentCulture =
    [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture =
    [System.Globalization.CultureInfo]::InvariantCulture

$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
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
    param(
        [string]$Label,
        [AllowNull()]$Expected,
        [AllowNull()]$Actual
    )
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
        if ([System.Text.Json.Nodes.JsonNode]::DeepEquals($left, $right)) {
            return
        }
    } catch {
        # Report through the common failure path.
    }
    $script:Failures++
    Write-Output "FAIL $Label"
    Write-Output "  expected: $Expected"
    Write-Output "  actual:   $Actual"
}

function ConvertTo-Base64Utf8 {
    param([Parameter(Mandatory)][string]$Value)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    return [Convert]::ToBase64String($bytes)
}

function Start-ContractMock {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Mode,
        [Parameter(Mandatory)][string]$Scratch,
        [Parameter(Mandatory)][string]$ContractPath
    )

    $portFile = Join-Path $Scratch "$Name.port"
    $logFile = Join-Path $Scratch "$Name.requests.jsonl"
    $serverOut = Join-Path $Scratch "$Name.server.out"
    $serverErr = Join-Path $Scratch "$Name.server.err"
    $process = Start-Process -FilePath 'python3' -ArgumentList @(
        (Join-Path $PSScriptRoot 'mock_nsx_policy.py'),
        $ContractPath,
        $portFile,
        $logFile,
        $Mode
    ) -PassThru -RedirectStandardOutput $serverOut `
      -RedirectStandardError $serverErr

    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while (-not (Test-Path -LiteralPath $portFile -PathType Leaf)) {
        if ($process.HasExited -or [DateTime]::UtcNow -gt $deadline) {
            $detail = Get-Content -LiteralPath $serverErr -Raw `
                -ErrorAction SilentlyContinue
            throw "loopback mock '$Name' failed to start: $detail"
        }
        Start-Sleep -Milliseconds 25
    }
    return [pscustomobject]@{
        Process = $process
        Port = [int](Get-Content -LiteralPath $portFile -Raw)
        LogFile = $logFile
    }
}

$manifestPath = Join-Path $root 'VcfNsxIpBlock/VcfNsxIpBlock.psd1'
$modulePath = Join-Path $root 'VcfNsxIpBlock/VcfNsxIpBlock.psm1'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $modulePath -PathType Leaf)) {
    Write-Output 'FAIL VcfNsxIpBlock module files are missing'
    exit 1
}

$prerequisite = Get-Module -ListAvailable -Name VMware.Sdk.Vcf.SddcManager |
    Sort-Object Version -Descending |
    Select-Object -First 1
Assert-True 'VCF PowerCLI prerequisite is installed' ($null -ne $prerequisite)

$manifest = Import-PowerShellDataFile -LiteralPath $manifestPath
$requiredNames = @($manifest.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ } else { $_.ModuleName }
})
Assert-True 'manifest requires VMware.Sdk.Vcf.SddcManager' (
    $requiredNames -ccontains 'VMware.Sdk.Vcf.SddcManager'
)
Assert-Eq 'manifest exports exactly two functions' (
    'New-VcfNsxIpAddressBlockModel,Set-VcfNsxIpAddressBlock'
) ((@($manifest.FunctionsToExport) | Sort-Object) -join ',')

$vendored = @(Get-ChildItem -LiteralPath $root -Recurse -File |
    Where-Object {
        $_.Extension -in @('.dll', '.nupkg') -or
        $_.Name -eq 'nsx_policy_api.yaml' -or
        $_.Name -match '^VMware\..*\.(psd1|psm1)$'
    })
Assert-Eq 'no VMware SDK, module, assembly, or specification is vendored' 0 $vendored.Count

$contractPath = Join-Path $root 'docs/contract.json'
$sourcesPath = Join-Path $root 'docs/official_sources.json'
$contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
$sources = Get-Content -LiteralPath $sourcesPath -Raw | ConvertFrom-Json
Assert-Eq 'contract base path' '/policy/api/v1' $contract.basePath
Assert-Eq 'contract has one operation' 1 @($contract.operations).Count
$operation = @($contract.operations)[0]
Assert-Eq 'contract operationId' 'CreateOrPatchIpAddressBlock' $operation.operationId
Assert-Eq 'contract method' 'PATCH' $operation.method
Assert-Eq 'contract operation path' '/infra/ip-blocks/{ip-block-id}' $operation.path
$successResponses = @($operation.responses.PSObject.Properties.Name |
    Where-Object { [int]$_ -ge 200 -and [int]$_ -lt 300 })
Assert-Eq 'contract documents only 200 as success' '200' (
    $successResponses -join ','
)
Assert-Eq 'contract commit matches provenance' (
    $sources.repository_commit_sha
) $contract.derived_from.commit_sha
Assert-Eq 'contract spec path matches provenance' (
    $sources.spec_path
) $contract.derived_from.path
Assert-Eq 'contract blob matches provenance' (
    $sources.spec_blob_sha
) $contract.derived_from.blob_sha
Assert-Eq 'provenance records one operation' 1 @($sources.operations).Count
$sourceOperation = @($sources.operations)[0]
Assert-Eq 'provenance operationId' $operation.operationId $sourceOperation.operationId
Assert-Eq 'operation provenance commit' (
    $sources.repository_commit_sha
) $sourceOperation.repository_commit_sha
Assert-Eq 'operation provenance spec path' (
    $sources.spec_path
) $sourceOperation.spec_path

Import-Module -Name $manifestPath -Force
$exports = @(Get-Command -Module VcfNsxIpBlock -CommandType Function |
    Select-Object -ExpandProperty Name |
    Sort-Object)
Assert-Eq 'runtime module exports exactly two functions' (
    'New-VcfNsxIpAddressBlockModel,Set-VcfNsxIpAddressBlock'
) ($exports -join ',')

$minimalModel = New-VcfNsxIpAddressBlockModel `
    -DisplayName 'edge pool' `
    -Cidrs @('10.42.0.0/16', '10.43.0.0/16')
Assert-Eq 'builder returns official generated type' (
    'VMware.Bindings.Nsx.Policy.Model.IpAddressBlock'
) $minimalModel.GetType().FullName
$expectedMinimal = @'
{
  "cidrs": [
    "10.42.0.0/16",
    "10.43.0.0/16"
  ],
  "display_name": "edge pool"
}
'@
Assert-JsonEq 'unset optional fields are omitted by generated serializer' (
    $expectedMinimal
) $minimalModel.ToJson()
$minimalMembers = @(
    ($minimalModel.ToJson() | ConvertFrom-Json -AsHashtable).Keys |
    Sort-Object
)
Assert-Eq 'minimal model exact JSON member set' (
    'cidrs,display_name'
) ($minimalMembers -join ',')

$optionalModel = New-VcfNsxIpAddressBlockModel `
    -DisplayName 'Ops v6' `
    -Cidrs @('2001:db8:42::/48') `
    -Description 'Reserved for operations' `
    -SubnetExclusive $false
Assert-Eq 'explicit false is preserved on the generated model' $false (
    $optionalModel.SubnetExclusive
)
$expectedOptional = @'
{
  "subnet_exclusive": false,
  "cidrs": [
    "2001:db8:42::/48"
  ],
  "display_name": "Ops v6",
  "description": "Reserved for operations"
}
'@
Assert-JsonEq 'explicit optional values serialize exactly' (
    $expectedOptional
) $optionalModel.ToJson()

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) (
    'vcf91-0052-' + [guid]::NewGuid().ToString('N')
)
$servers = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
try {
    New-Item -ItemType Directory -Path $scratch > $null
    $primary = Start-ContractMock `
        -Name 'drop' `
        -Mode 'drop-after-apply' `
        -Scratch $scratch `
        -ContractPath $contractPath
    $servers.Add($primary.Process)
    $baseUri = [uri]"http://127.0.0.1:$($primary.Port)"

    $firstResult = Set-VcfNsxIpAddressBlock `
        -BaseUri $baseUri `
        -IpBlockId 'finance block/blue' `
        -DisplayName 'edge pool' `
        -Cidrs @('10.42.0.0/16', '10.43.0.0/16') `
        -AccessToken 'loopback-token'
    Assert-Eq 'first result operationId' (
        'CreateOrPatchIpAddressBlock'
    ) $firstResult.OperationId
    Assert-Eq 'first result id' 'finance block/blue' $firstResult.IpBlockId
    Assert-Eq 'uncertain result is retried exactly once' 2 $firstResult.Attempts

    $secondResult = Set-VcfNsxIpAddressBlock `
        -BaseUri $baseUri `
        -IpBlockId 'ops-v6' `
        -DisplayName 'Ops v6' `
        -Cidrs @('2001:db8:42::/48') `
        -AccessToken 'loopback-token' `
        -Description 'Reserved for operations' `
        -SubnetExclusive $false
    Assert-Eq 'normal success uses one attempt' 1 $secondResult.Attempts

    $logDeadline = [DateTime]::UtcNow.AddSeconds(5)
    do {
        $lines = @(Get-Content -LiteralPath $primary.LogFile `
            -ErrorAction SilentlyContinue |
            Where-Object { $_.Trim().Length -gt 0 })
        if ($lines.Count -ge 3) { break }
        Start-Sleep -Milliseconds 25
    } while ([DateTime]::UtcNow -lt $logDeadline)
    $entries = @($lines | ForEach-Object { $_ | ConvertFrom-Json })
    Assert-Eq 'exact request count' 3 $entries.Count

    if ($entries.Count -eq 3) {
        $expectedTargets = @(
            '/policy/api/v1/infra/ip-blocks/finance%20block%2Fblue',
            '/policy/api/v1/infra/ip-blocks/finance%20block%2Fblue',
            '/policy/api/v1/infra/ip-blocks/ops-v6'
        )
        $expectedBodies = @(
            $minimalModel.ToJson(),
            $minimalModel.ToJson(),
            $optionalModel.ToJson()
        )
        for ($index = 0; $index -lt 3; $index++) {
            $entry = $entries[$index]
            Assert-Eq "request $index operationId" (
                'CreateOrPatchIpAddressBlock'
            ) $entry.operationId
            Assert-Eq "request $index method" 'PATCH' $entry.method
            Assert-Eq "request $index target" (
                $expectedTargets[$index]
            ) $entry.target
            Assert-Eq "request $index has no query" '' $entry.query
            Assert-Eq "request $index authorization" (
                'Bearer loopback-token'
            ) $entry.authorization
            Assert-Eq "request $index accept" 'application/json' $entry.accept
            Assert-Eq "request $index content type" (
                'application/json'
            ) $entry.content_type
            Assert-Eq "request $index exact UTF-8 body bytes" (
                ConvertTo-Base64Utf8 -Value $expectedBodies[$index]
            ) $entry.body_base64
            Assert-Eq "request $index content length" (
                [System.Text.Encoding]::UTF8.GetByteCount($expectedBodies[$index])
            ) $entry.content_length
        }
        Assert-Eq 'retry URI is identical' $entries[0].target $entries[1].target
        Assert-Eq 'retry body bytes are identical' (
            $entries[0].body_base64
        ) $entries[1].body_base64
        Assert-Eq 'retry token is identical' (
            $entries[0].authorization
        ) $entries[1].authorization
        Assert-Eq 'first response was dropped after apply' (
            'connection_dropped_after_apply'
        ) $entries[0].outcome
        Assert-Eq 'first apply changes one resource' $true $entries[0].changed
        Assert-Eq 'identical retry does not add an effect' $false $entries[1].changed
        Assert-Eq 'retry leaves one resource' 1 $entries[1].resource_count
        Assert-Eq 'retry leaves mutation effect count at one' 1 $entries[1].effect_count
        Assert-Eq 'second id creates the second resource' 2 $entries[2].resource_count
        Assert-Eq 'second id increments effect count once' 2 $entries[2].effect_count
        Assert-JsonEq 'minimal wire JSON has exact members' (
            $expectedMinimal
        ) $entries[0].body_utf8
        Assert-JsonEq 'optional wire JSON has exact members' (
            $expectedOptional
        ) $entries[2].body_utf8
    }

    $transient = Start-ContractMock `
        -Name 'transient' `
        -Mode 'transient-503' `
        -Scratch $scratch `
        -ContractPath $contractPath
    $servers.Add($transient.Process)
    $transientResult = Set-VcfNsxIpAddressBlock `
        -BaseUri ([uri]"http://127.0.0.1:$($transient.Port)") `
        -IpBlockId 'retry-503' `
        -DisplayName 'retry-503' `
        -Cidrs @('10.50.0.0/16') `
        -AccessToken 'loopback-token'
    Assert-Eq 'documented 503 is retried once' 2 $transientResult.Attempts
    $transientLines = @(Get-Content -LiteralPath $transient.LogFile |
        Where-Object { $_.Trim().Length -gt 0 })
    $transientEntries = @($transientLines |
        ForEach-Object { $_ | ConvertFrom-Json })
    Assert-Eq '503 scenario exact request count' 2 $transientEntries.Count
    if ($transientEntries.Count -eq 2) {
        Assert-Eq '503 first response status' 503 $transientEntries[0].status
        Assert-Eq '503 retry response status' 200 $transientEntries[1].status
        Assert-Eq '503 retry target is identical' (
            $transientEntries[0].target
        ) $transientEntries[1].target
        Assert-Eq '503 retry body is identical' (
            $transientEntries[0].body_base64
        ) $transientEntries[1].body_base64
        Assert-Eq '503 response does not apply mutation' 0 (
            $transientEntries[0].effect_count
        )
        Assert-Eq '503 retry applies one mutation' 1 (
            $transientEntries[1].effect_count
        )
    }

    $badRequest = Start-ContractMock `
        -Name 'bad-request' `
        -Mode 'always-400' `
        -Scratch $scratch `
        -ContractPath $contractPath
    $servers.Add($badRequest.Process)
    $badRequestThrew = $false
    try {
        Set-VcfNsxIpAddressBlock `
            -BaseUri ([uri]"http://127.0.0.1:$($badRequest.Port)") `
            -IpBlockId 'do-not-retry' `
            -DisplayName 'do-not-retry' `
            -Cidrs @('10.60.0.0/16') `
            -AccessToken 'loopback-token' > $null
    } catch {
        $badRequestThrew = $true
    }
    Assert-True '400 is terminal' $badRequestThrew
    $badRequestLines = @(Get-Content -LiteralPath $badRequest.LogFile |
        Where-Object { $_.Trim().Length -gt 0 })
    Assert-Eq '400 is not retried' 1 $badRequestLines.Count
    if ($badRequestLines.Count -eq 1) {
        $badRequestEntry = $badRequestLines[0] | ConvertFrom-Json
        Assert-Eq '400 response status' 400 $badRequestEntry.status
        Assert-Eq '400 does not apply mutation' 0 $badRequestEntry.effect_count
    }
} catch {
    $script:Failures++
    Write-Output "FAIL verifier exception: $($_.Exception.Message)"
} finally {
    foreach ($server in $servers) {
        if (-not $server.HasExited) {
            $server.Kill()
            $server.WaitForExit()
        }
    }
    Remove-Item -LiteralPath $scratch -Recurse -Force `
        -ErrorAction SilentlyContinue
    Remove-Module VcfNsxIpBlock -Force -ErrorAction SilentlyContinue
}

if ($script:Failures -gt 0) {
    Write-Output "$($script:Failures) of $($script:Checks) checks failed"
    exit 1
}
Write-Output "all checks passed ($($script:Checks) checks)"
exit 0
