# Protected acceptance verifier for the VCF 9.1 depot retry module.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$PSStyle.OutputRendering = 'PlainText'
[System.Globalization.CultureInfo]::CurrentCulture =
    [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture =
    [System.Globalization.CultureInfo]::InvariantCulture

$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
$script:Checks = 0
$script:Failures = 0

function Assert-True {
    param([Parameter(Mandatory)] [string] $Label, [bool] $Condition)
    $script:Checks++
    if ($Condition) { return }
    $script:Failures++
    Write-Output "FAIL $Label"
}

function Assert-Eq {
    param([Parameter(Mandatory)] [string] $Label, $Expected, $Actual)
    $script:Checks++
    if ("$Expected" -ceq "$Actual") { return }
    $script:Failures++
    Write-Output "FAIL $Label"
    Write-Output "  expected: $Expected"
    Write-Output "  actual:   $Actual"
}

function Get-JsonPropertyNames {
    param([Parameter(Mandatory)] $InputObject)
    @($InputObject.PSObject.Properties.Name | Sort-Object)
}

$modulePath = Join-Path $Root 'VcfDepotRetry.psm1'
if (-not (Test-Path -LiteralPath $modulePath -PathType Leaf)) {
    Write-Output 'FAIL VcfDepotRetry.psm1 is missing'
    exit 1
}

# Fail closed if any protected contract fixture was changed.
$protectedHashes = @{
    (Join-Path $Root 'docs/contract.json') = '941c6f5ef58e5c5c7825dd65624e4c9da7a6c57201b128d4935b64b6fd0b6afb'
    (Join-Path $Root 'docs/official_sources.json') = 'c5930b82a30202d63878606bda909c4ce323bf824cddb616e05a6b25a92dfa79'
    (Join-Path $Root '.protected/mock_sddc_manager.py') = 'df4534c34141558c22560beb754305cb8753431ccc099a61f7bbd4f00358bd91'
    (Join-Path $Root '.gitignore') = '2eab86595eefa9c93d8c44f171b67960bd1fdffe1a31613167fac1908a0708ae'
}
foreach ($entry in $protectedHashes.GetEnumerator()) {
    $actual = (Get-FileHash -LiteralPath $entry.Key -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-Eq "protected hash $([IO.Path]::GetFileName($entry.Key))" `
        $entry.Value $actual
}

$contract = Get-Content -LiteralPath (Join-Path $Root 'docs/contract.json') `
    -Raw | ConvertFrom-Json
$sources = Get-Content -LiteralPath (Join-Path $Root 'docs/official_sources.json') `
    -Raw | ConvertFrom-Json
$expectedSha = '3949fc33339fc5ea1b77eadb258f1cf49aa88e26'
$expectedSpec = 'specifications/sddc-manager/sddc-manager-openapi.json'
$expectedOperations = 'createToken,updateDepotSettings'
Assert-Eq 'contract format' 'focused-openapi-projection-v1' `
    $contract.contract_format
Assert-Eq 'contract pins OpenAPI 3.0.1' '3.0.1' `
    $contract.derived_from.openapi
Assert-Eq 'contract pins VCF 9.1' '9.1.0.0' `
    $contract.derived_from.info_version
Assert-Eq 'contract source commit' $expectedSha `
    $contract.derived_from.repository_commit_sha
Assert-Eq 'contract source path' $expectedSpec `
    $contract.derived_from.spec_path
Assert-Eq 'contract operationIds' $expectedOperations `
    (($contract.operations.operationId) -join ',')
Assert-Eq 'contract methods' 'POST,PUT' `
    (($contract.operations.method) -join ',')
Assert-Eq 'contract paths' '/v1/tokens,/v1/system/settings/depot' `
    (($contract.operations.path) -join ',')
Assert-Eq 'official source repository commit' $expectedSha `
    $sources.repository.commit_sha
Assert-Eq 'official source specification path' $expectedSpec `
    $sources.specification.path
Assert-Eq 'official source operationIds' $expectedOperations `
    (($sources.operations.operationId) -join ',')
foreach ($operation in $sources.operations) {
    Assert-Eq "source $($operation.operationId) repeats commit" $expectedSha `
        $operation.repository_commit_sha
    Assert-Eq "source $($operation.operationId) repeats path" $expectedSpec `
        $operation.spec_path
}
Assert-Eq 'DepotAccount projected property order' `
    'username,password,status,message,downloadToken,downloadActivationCode' `
    (($contract.schemas.DepotAccount.properties.PSObject.Properties.Name) -join ',')
Assert-Eq 'DepotSettings projected property order' `
    'vmwareAccount,offlineAccount,depotConfiguration' `
    (($contract.schemas.DepotSettings.properties.PSObject.Properties.Name) -join ',')

# Require the official SDK surface and reject parallel transports or vendoring.
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $modulePath,
    [ref] $tokens,
    [ref] $parseErrors
)
Assert-Eq 'module parses without errors' 0 @($parseErrors).Count
$commandNames = @(
    $ast.FindAll(
        { param($Node) $Node -is [System.Management.Automation.Language.CommandAst] },
        $true
    ) | ForEach-Object { $_.GetCommandName() } |
        Where-Object { $null -ne $_ }
)
Assert-True 'module imports the VMware SDK' `
    ($commandNames -contains 'Import-Module')
Assert-True 'module resolves the exact SDK operation' `
    ($commandNames -contains 'Get-VcfSddcManagerOperation')
Assert-True 'module constructs DepotAccount with the SDK' `
    ($commandNames -contains 'Initialize-VcfDepotAccount')
Assert-True 'module constructs DepotSettings with the SDK' `
    ($commandNames -contains 'Initialize-VcfDepotSettings')
foreach ($forbidden in @(
    'Invoke-WebRequest',
    'Invoke-RestMethod',
    'curl',
    'curl.exe',
    'wget'
)) {
    Assert-True "module does not call $forbidden" `
        ($commandNames -notcontains $forbidden)
}
$sourceText = Get-Content -LiteralPath $modulePath -Raw
foreach ($forbiddenType in @(
    'System.Net.Http.HttpClient',
    'System.Net.WebRequest',
    'TcpClient',
    'Socket'
)) {
    Assert-True "module does not use $forbiddenType" `
        ($sourceText -notmatch [regex]::Escape($forbiddenType))
}
$vendored = @(
    Get-ChildItem -LiteralPath $Root -Recurse -File |
        Where-Object {
            $_.Extension.ToLowerInvariant() -in @(
                '.dll', '.nupkg', '.snupkg', '.zip'
            )
        }
)
Assert-Eq 'no binary dependency is vendored' 0 $vendored.Count

$runtimeDir = Join-Path $Root '_verification'
$serverProcess = $null
$connection = $null
try {
    New-Item -ItemType Directory -Force -Path $runtimeDir > $null
    $portFile = Join-Path $runtimeDir 'port.txt'
    $logFile = Join-Path $runtimeDir 'requests.jsonl'
    $runtimeInfoFile = Join-Path $runtimeDir 'runtime.json'
    $serverOut = Join-Path $runtimeDir 'server.out'
    $serverErr = Join-Path $runtimeDir 'server.err'
    Remove-Item -LiteralPath @(
        $portFile,
        $logFile,
        $runtimeInfoFile,
        $serverOut,
        $serverErr
    ) -ErrorAction SilentlyContinue

    $serverProcess = Start-Process -FilePath 'python3' `
        -ArgumentList @(
            (Join-Path $Root '.protected/mock_sddc_manager.py'),
            $portFile,
            $logFile,
            $runtimeInfoFile
        ) `
        -PassThru `
        -RedirectStandardOutput $serverOut `
        -RedirectStandardError $serverErr

    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (
        -not (Test-Path -LiteralPath $portFile -PathType Leaf) -or
        -not (Test-Path -LiteralPath $runtimeInfoFile -PathType Leaf)
    ) {
        if ($serverProcess.HasExited -or [DateTime]::UtcNow -gt $deadline) {
            $detail = Get-Content -LiteralPath $serverErr -Raw `
                -ErrorAction SilentlyContinue
            throw "loopback mock failed to start: $detail"
        }
        Start-Sleep -Milliseconds 40
    }

    $port = [int] (Get-Content -LiteralPath $portFile -Raw).Trim()
    $runtimeInfo = Get-Content -LiteralPath $runtimeInfoFile -Raw |
        ConvertFrom-Json

    Import-Module 'VMware.Sdk.Vcf.SddcManager' `
        -MinimumVersion '13.5.0.25380678' `
        -Force `
        -ErrorAction Stop
    foreach ($sdkCommand in @(
        'Get-VcfSddcManagerOperation',
        'Initialize-VcfDepotAccount',
        'Initialize-VcfDepotSettings'
    )) {
        $command = Get-Command $sdkCommand -ErrorAction Stop
        Assert-Eq "$sdkCommand comes from the installed VMware SDK" `
            'VMware.Sdk.Vcf.SddcManager' $command.Source
    }
    $resolved = @(Get-VcfSddcManagerOperation -Name 'updateDepotSettings')
    Assert-Eq 'installed SDK resolves updateDepotSettings once' 1 $resolved.Count
    Assert-Eq 'resolved operation path matches contract' `
        '/v1/system/settings/depot' $resolved[0].Path

    Import-Module $modulePath -Force -ErrorAction Stop
    $exports = @(
        Get-Command -Module VcfDepotRetry -CommandType Function |
            Select-Object -ExpandProperty Name
    )
    Assert-Eq 'module exports exactly one function' `
        'Set-VcfDepotSettingsRetrySafe' ($exports -join ',')

    $securePassword = ConvertTo-SecureString `
        ([string] $runtimeInfo.password) -AsPlainText -Force
    $connection = Connect-VcfSddcManagerServer `
        -Server '127.0.0.1' `
        -Port $port `
        -Protocol http `
        -User ([string] $runtimeInfo.username) `
        -Password $securePassword `
        -NotDefault `
        -ErrorAction Stop

    $result = Set-VcfDepotSettingsRetrySafe `
        -Server $connection `
        -DownloadToken ([string] $runtimeInfo.downloadToken) `
        -MaxAttempts 2 `
        -ErrorAction Stop
    Assert-Eq 'SDK success result preserves download token' `
        $runtimeInfo.downloadToken $result.VmwareAccount.DownloadToken

    $logLines = @(
        Get-Content -LiteralPath $logFile |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    $requests = @($logLines | ForEach-Object { $_ | ConvertFrom-Json })
    Assert-Eq 'exact wire request count' 4 $requests.Count
    Assert-Eq 'exact operation sequence' `
        'createToken,,updateDepotSettings,updateDepotSettings' `
        (($requests.operationId | ForEach-Object { [string] $_ }) -join ',')
    Assert-Eq 'mock sees only named contract operationIds' `
        $expectedOperations `
        ((
            @(
                $requests.operationId |
                    Where-Object { $null -ne $_ } |
                    Sort-Object -Unique
            )
        ) -join ',')
    Assert-True 'every target omits a query string' `
        (@($requests | Where-Object { $_.rawQuery -cne '' }).Count -eq 0)
    Assert-True 'every request stays on the loopback authority' `
        (@($requests | Where-Object {
            $_.headers.host -cne "127.0.0.1:$port"
        }).Count -eq 0)
    Assert-True 'every SDK request accepts JSON' `
        (@($requests | Where-Object {
            $_.headers.accept -notlike '*application/json*'
        }).Count -eq 0)

    $tokenRequests = @(
        $requests | Where-Object operationId -CEQ 'createToken'
    )
    Assert-Eq 'one createToken request' 1 $tokenRequests.Count
    Assert-Eq 'createToken method' 'POST' $tokenRequests[0].method
    Assert-Eq 'createToken target' '/v1/tokens' $tokenRequests[0].rawTarget
    Assert-Eq 'createToken status' 201 $tokenRequests[0].responseStatus
    Assert-Eq 'createToken carries no bearer token' '' `
        $tokenRequests[0].authorization
    Assert-True 'createToken content type is JSON' `
        ($tokenRequests[0].contentType -like 'application/json*')
    $tokenBody = $tokenRequests[0].body | ConvertFrom-Json
    Assert-Eq 'createToken body has only bound fields' 'password,username' `
        ((Get-JsonPropertyNames $tokenBody) -join ',')
    Assert-Eq 'createToken username reaches SDK' `
        $runtimeInfo.username $tokenBody.username
    Assert-Eq 'createToken password reaches SDK' `
        $runtimeInfo.password $tokenBody.password

    $versionProbes = @(
        $requests | Where-Object {
            $null -eq $_.operationId -and
            $_.method -ceq 'GET' -and
            $_.path -ceq '/v1/sddc-manager'
        }
    )
    Assert-Eq 'one SDK connection version probe' 1 $versionProbes.Count
    Assert-Eq 'version probe has no body' 0 $versionProbes[0].bodyLength
    Assert-Eq 'version probe has exact bearer token' `
        "Bearer $($runtimeInfo.accessToken)" $versionProbes[0].authorization

    $updates = @(
        $requests | Where-Object operationId -CEQ 'updateDepotSettings'
    )
    Assert-Eq 'HTTP 500 is retried exactly once' 2 $updates.Count
    Assert-Eq 'both mutations use PUT' 'PUT,PUT' `
        (($updates.method) -join ',')
    Assert-Eq 'both mutation targets are exact' `
        '/v1/system/settings/depot,/v1/system/settings/depot' `
        (($updates.rawTarget) -join ',')
    Assert-Eq 'first committed response is transient 500' 500 `
        $updates[0].responseStatus
    Assert-Eq 'second identical PUT is accepted' 202 `
        $updates[1].responseStatus
    Assert-True 'both mutations have JSON content type' `
        (@($updates | Where-Object {
            $_.contentType -notlike 'application/json*'
        }).Count -eq 0)
    Assert-True 'both mutations have exact bearer token' `
        (@($updates | Where-Object {
            $_.authorization -cne "Bearer $($runtimeInfo.accessToken)"
        }).Count -eq 0)
    Assert-Eq 'retry body bytes are identical' $updates[0].body $updates[1].body

    $updateBody = $updates[0].body | ConvertFrom-Json
    Assert-Eq 'body has exactly vmwareAccount at top level' `
        'vmwareAccount' ((Get-JsonPropertyNames $updateBody) -join ',')
    Assert-Eq 'account has exactly downloadToken' `
        'downloadToken' `
        ((Get-JsonPropertyNames $updateBody.vmwareAccount) -join ',')
    Assert-Eq 'downloadToken reaches the SDK operation' `
        $runtimeInfo.downloadToken $updateBody.vmwareAccount.downloadToken
    foreach ($omitted in @(
        'username',
        'password',
        'status',
        'message',
        'downloadActivationCode'
    )) {
        Assert-True "account omits unset optional $omitted" `
            ($updateBody.vmwareAccount.PSObject.Properties.Name -cnotcontains $omitted)
    }
    foreach ($omitted in @('offlineAccount', 'depotConfiguration')) {
        Assert-True "body omits unset optional $omitted" `
            ($updateBody.PSObject.Properties.Name -cnotcontains $omitted)
    }
    Assert-Eq 'first PUT applies one desired-state effect' 1 `
        $updates[0].mutationEffectCount
    Assert-Eq 'identical retry does not duplicate the effect' 1 `
        $updates[1].mutationEffectCount
} catch {
    $script:Failures++
    Write-Output "FAIL verifier raised: $($_.Exception.Message)"
    Write-Output $_.ScriptStackTrace
} finally {
    if ($null -ne $connection) {
        try {
            Disconnect-VcfSddcManagerServer -Server $connection -Force `
                -ErrorAction SilentlyContinue > $null
        } catch {}
    }
    if ($null -ne $serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
    }
}

Write-Output "checks=$($script:Checks) failures=$($script:Failures)"
if ($script:Failures -gt 0) { exit 1 }
Write-Output 'ALL TESTS PASSED'
exit 0
