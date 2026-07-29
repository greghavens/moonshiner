# Protected acceptance verifier for VcfUserAccess.psm1.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$PSStyle.OutputRendering = 'PlainText'
[System.Globalization.CultureInfo]::CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Globalization.CultureInfo]::CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture

$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$script:Checks = 0
$script:Failures = 0

function Assert-True {
    param([string]$Label, [bool]$Condition)
    $script:Checks++
    if ($Condition) { return }
    $script:Failures++
    Write-Output "FAIL $Label"
}

function Assert-Equal {
    param([string]$Label, $Expected, $Actual)
    $script:Checks++
    if ("$Expected" -ceq "$Actual") { return }
    $script:Failures++
    Write-Output "FAIL $Label"
    Write-Output "  expected: $Expected"
    Write-Output "  actual:   $Actual"
}

$ModuleFile = Join-Path $Root 'VcfUserAccess.psm1'
if (-not (Test-Path -LiteralPath $ModuleFile -PathType Leaf)) {
    Write-Output 'FAIL VcfUserAccess.psm1 is missing'
    exit 1
}

# Fail closed if the protected provenance and contract are no longer the pinned subset.
$Sources = Get-Content -LiteralPath (Join-Path $Root 'docs/official_sources.json') -Raw | ConvertFrom-Json
$Contract = Get-Content -LiteralPath (Join-Path $Root 'docs/contract.json') -Raw | ConvertFrom-Json
$ExpectedSha = '3949fc33339fc5ea1b77eadb258f1cf49aa88e26'
$ExpectedSpecPath = 'specifications/sddc-manager/sddc-manager-openapi.json'
$ExpectedOperations = 'addUsers,createToken,getUsers'
Assert-Equal 'official source commit is pinned' $ExpectedSha $Sources.repository_commit_sha
Assert-Equal 'official source spec path is exact' $ExpectedSpecPath $Sources.spec_path
Assert-Equal 'official source records every operationId' $ExpectedOperations `
    ((@($Sources.operations.operationId) | Sort-Object) -join ',')
Assert-Equal 'contract source commit is pinned' $ExpectedSha $Contract.'x-derived-from'.commit
Assert-Equal 'contract source path is exact' $ExpectedSpecPath $Contract.'x-derived-from'.path
$ContractOperations = foreach ($PathProperty in $Contract.paths.PSObject.Properties) {
    foreach ($MethodProperty in $PathProperty.Value.PSObject.Properties) {
        $MethodProperty.Value.operationId
    }
}
Assert-Equal 'contract contains exactly the named operations' $ExpectedOperations `
    ((@($ContractOperations) | Sort-Object) -join ',')

# Require actual VMware SDK operation/model use, not a parallel raw HTTP client.
$Tokens = $null
$ParseErrors = $null
$Ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $ModuleFile, [ref]$Tokens, [ref]$ParseErrors)
Assert-Equal 'module parses without PowerShell errors' 0 @($ParseErrors).Count
$CommandNames = @(
    $Ast.FindAll(
        { param($Node) $Node -is [System.Management.Automation.Language.CommandAst] },
        $true
    ) | ForEach-Object { $_.GetCommandName() } | Where-Object { $null -ne $_ }
)
Assert-True 'module resolves generated SDK operations' `
    ($CommandNames -contains 'Get-VcfSddcManagerOperation')
Assert-True 'module constructs the SDK RoleReference model' `
    ($CommandNames -contains 'Initialize-VcfRoleReference')
Assert-True 'module constructs the SDK User model' `
    ($CommandNames -contains 'Initialize-VcfUser')
Assert-True 'module does not use Invoke-RestMethod' `
    ($CommandNames -notcontains 'Invoke-RestMethod')
Assert-True 'module does not use Invoke-WebRequest' `
    ($CommandNames -notcontains 'Invoke-WebRequest')
Assert-True 'module does not launch an external HTTP client' `
    (-not ($CommandNames | Where-Object { $_ -in @('curl', 'curl.exe', 'wget') }))

$Runtime = Join-Path $Root '_test'
$ServerProcess = $null
$Connection = $null
try {
    New-Item -ItemType Directory -Force -Path $Runtime > $null
    $PortFile = Join-Path $Runtime 'port.txt'
    $LogFile = Join-Path $Runtime 'requests.jsonl'
    Remove-Item -LiteralPath $PortFile -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $LogFile -ErrorAction SilentlyContinue
    $ServerProcess = Start-Process -FilePath 'python3' `
        -ArgumentList @(
            (Join-Path $Root 'tools/mock_sddc_manager.py'),
            $PortFile,
            $LogFile
        ) `
        -PassThru `
        -RedirectStandardOutput (Join-Path $Runtime 'server.out') `
        -RedirectStandardError (Join-Path $Runtime 'server.err')

    $Deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Path -LiteralPath $PortFile -PathType Leaf)) {
        if ($ServerProcess.HasExited -or [DateTime]::UtcNow -gt $Deadline) {
            $ServerError = Get-Content -LiteralPath (Join-Path $Runtime 'server.err') `
                -Raw -ErrorAction SilentlyContinue
            throw "loopback mock failed to start: $ServerError"
        }
        Start-Sleep -Milliseconds 50
    }
    $Port = [int](Get-Content -LiteralPath $PortFile -Raw).Trim()

    Import-Module VMware.Sdk.Vcf.SddcManager -ErrorAction Stop
    Import-Module $ModuleFile -Force -ErrorAction Stop

    $Exported = @(
        Get-Command -Module VcfUserAccess -CommandType Function |
            Select-Object -ExpandProperty Name |
            Sort-Object
    )
    Assert-Equal 'module exports exactly the two requested functions' `
        'Get-VcfUserAccess,Grant-VcfUserAccess' ($Exported -join ',')

    $Password = ConvertTo-SecureString 'loopback-only-password' -AsPlainText -Force
    $Connection = Connect-VcfSddcManagerServer `
        -Server '127.0.0.1' `
        -Port $Port `
        -Protocol http `
        -User 'svc-access' `
        -Password $Password `
        -NotDefault `
        -ErrorAction Stop

    # The mock reverses, then restores, element order on consecutive responses.
    $FirstRead = @(Get-VcfUserAccess -Server $Connection)
    $SecondRead = @(Get-VcfUserAccess -Server $Connection)
    Assert-Equal 'first collection response is sorted deterministically' `
        'zulu,Alpha' (($FirstRead | ForEach-Object Name) -join ',')
    Assert-Equal 'flipped collection response remains sorted' `
        'zulu,Alpha' (($SecondRead | ForEach-Object Name) -join ',')
    Assert-Equal 'SDK user ids are preserved' `
        'user-zulu,user-alpha' (($FirstRead | ForEach-Object Id) -join ',')

    $FirstGrant = @(
        Grant-VcfUserAccess `
            -Server $Connection `
            -Name 'ops-bot' `
            -Domain 'rainpole.io' `
            -Type SERVICE `
            -RoleId 'OPERATOR'
    )
    $RetryGrant = @(
        Grant-VcfUserAccess `
            -Server $Connection `
            -Name 'OPS-BOT' `
            -Domain 'RAINPOLE.IO' `
            -Type service `
            -RoleId 'OPERATOR'
    )
    Assert-Equal 'grant response is sorted despite mock response order' `
        'zulu,ops-bot,Alpha' (($FirstGrant | ForEach-Object Name) -join ',')
    Assert-Equal 'retry response is sorted and keeps the original resource' `
        'zulu,ops-bot,Alpha' (($RetryGrant | ForEach-Object Name) -join ',')
    Assert-Equal 'retry has one matching logical identity' 1 `
        @($RetryGrant | Where-Object {
            $_.Name -ieq 'ops-bot' -and
            $_.Domain -ieq 'rainpole.io' -and
            $_.Type -ieq 'SERVICE'
        }).Count

    $Conflict = $null
    try {
        Grant-VcfUserAccess `
            -Server $Connection `
            -Name 'ops-bot' `
            -Domain 'rainpole.io' `
            -Type SERVICE `
            -RoleId 'ADMIN' > $null
    } catch {
        $Conflict = $_
    }
    Assert-True 'existing identity with another role fails' ($null -ne $Conflict)

    # The request log is a file, not an extra mock API operation.
    $LogLines = @(Get-Content -LiteralPath $LogFile | Where-Object { $_.Trim() })
    $Requests = @($LogLines | ForEach-Object { $_ | ConvertFrom-Json })
    $TokenRequests = @($Requests | Where-Object {
        $_.method -eq 'POST' -and $_.path -eq '/v1/tokens'
    })
    $UserReads = @($Requests | Where-Object {
        $_.method -eq 'GET' -and $_.path -eq '/v1/users'
    })
    $UserAdds = @($Requests | Where-Object {
        $_.method -eq 'POST' -and $_.path -eq '/v1/users'
    })
    Assert-Equal 'SDK connection uses createToken exactly once' 1 $TokenRequests.Count
    Assert-True 'read-before-write and retries all read current state' ($UserReads.Count -ge 5)
    Assert-Equal 'safe retry sends exactly one addUsers mutation' 1 $UserAdds.Count
    Assert-True 'SDK calls carry the access token' `
        (-not ($UserReads | Where-Object {
            $_.authorization -cne 'Bearer loopback-access-token'
        }))
    $AddedBody = @($UserAdds[0].body | ConvertFrom-Json)
    Assert-Equal 'addUsers receives one SDK User model' 1 $AddedBody.Count
    Assert-Equal 'created user name reaches the contract operation' 'ops-bot' $AddedBody[0].name
    Assert-Equal 'created user domain reaches the contract operation' 'rainpole.io' $AddedBody[0].domain
    Assert-Equal 'created user type reaches the contract operation' 'SERVICE' $AddedBody[0].type
    Assert-Equal 'created role reference reaches the contract operation' 'OPERATOR' $AddedBody[0].role.id
    # /v1/sddc-manager is the SDK's connection handshake, not a contract operation.
    Assert-True 'mock received only contract and handshake paths' `
        (-not ($Requests | Where-Object {
            $_.path -notin @('/v1/tokens', '/v1/sddc-manager', '/v1/users')
        }))
} catch {
    $script:Failures++
    Write-Output "FAIL verifier raised: $($_.Exception.Message)"
    Write-Output "$($_.ScriptStackTrace)"
} finally {
    if ($null -ne $Connection) {
        try {
            Disconnect-VcfSddcManagerServer -Server $Connection -Force `
                -ErrorAction SilentlyContinue > $null
        } catch {}
    }
    if ($null -ne $ServerProcess -and -not $ServerProcess.HasExited) {
        Stop-Process -Id $ServerProcess.Id -Force -ErrorAction SilentlyContinue
    }
}

Write-Output "checks=$($script:Checks) failures=$($script:Failures)"
if ($script:Failures -gt 0) { exit 1 }
Write-Output 'ALL TESTS PASSED'
exit 0
