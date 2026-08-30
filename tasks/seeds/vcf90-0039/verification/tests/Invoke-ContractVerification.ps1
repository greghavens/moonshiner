#Requires -Version 7.2
<#
.SYNOPSIS
    Contract verification for VcenterSecretRotation.

.DESCRIPTION
    Drives the module against the loopback mock in tests/mock and asserts the exact wire shape
    of every request it produced, plus the ordering guarantee that the retiring session outlives
    the work that is still bound to it. No VMware endpoint is contacted.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ModulePath = Join-Path $RepoRoot 'src/VcenterSecretRotation/VcenterSecretRotation.psd1'
$ContractPath = Join-Path $RepoRoot 'docs/contract.json'
$SourcesPath = Join-Path $RepoRoot 'docs/official_sources.json'
$MockScript = Join-Path $PSScriptRoot 'mock/vcenter_contract_mock.py'
$CertScript = Join-Path $PSScriptRoot 'mock/New-MockCertificate.ps1'

# Fixture identities, matching tests/mock/vcenter_contract_mock.py.
$Account = 'svc-automation@vsphere.local'
$NewSecret = 'N3w-Secret-Rotate!'
$RetiringSessionId = '0ldsess1on0000000000000000000000'
$RotatedSessionId = 'n3wsess1on1111111111111111111111'
$DrainService = @('com.vmware.vcenter.vm')

$script:Failures = [System.Collections.Generic.List[string]]::new()
$script:Checks = 0

function Test-That {
    param([string] $Name, [bool] $Condition, [string] $Detail = '')
    $script:Checks++
    if ($Condition) {
        Write-Host ("  PASS  {0}" -f $Name)
    }
    else {
        Write-Host ("  FAIL  {0}" -f $Name) -ForegroundColor Red
        if ($Detail) { Write-Host ("        {0}" -f $Detail) -ForegroundColor Red }
        $script:Failures.Add(("{0}{1}" -f $Name, $(if ($Detail) { " -- $Detail" } else { '' })))
    }
}

function Test-Equal {
    param([string] $Name, $Expected, $Actual)
    $expectedText = if ($null -eq $Expected) { '<null>' } else { [string]$Expected }
    $actualText = if ($null -eq $Actual) { '<null>' } else { [string]$Actual }
    Test-That -Name $Name -Condition ($expectedText -ceq $actualText) `
        -Detail ("expected '{0}', got '{1}'" -f $expectedText, $actualText)
}

function ConvertTo-CanonicalJson {
    param($Value)
    if ($null -eq $Value) { return 'null' }
    if ($Value -is [System.Collections.IDictionary]) {
        $parts = foreach ($key in ($Value.Keys | Sort-Object -CaseSensitive)) {
            '{0}:{1}' -f (ConvertTo-Json -InputObject ([string]$key) -Compress), (ConvertTo-CanonicalJson -Value $Value[$key])
        }
        return '{' + ($parts -join ',') + '}'
    }
    if ($Value -is [string]) { return ConvertTo-Json -InputObject $Value -Compress }
    if ($Value -is [System.Collections.IEnumerable]) {
        $parts = foreach ($item in $Value) { ConvertTo-CanonicalJson -Value $item }
        return '[' + ($parts -join ',') + ']'
    }
    if ($Value -is [bool]) { return $Value.ToString().ToLowerInvariant() }
    return ConvertTo-Json -InputObject $Value -Compress
}

function ConvertTo-CanonicalText {
    param([string] $Json)
    if ([string]::IsNullOrWhiteSpace($Json)) { return '<empty>' }
    try {
        return ConvertTo-CanonicalJson -Value ($Json | ConvertFrom-Json -AsHashtable -Depth 20)
    }
    catch {
        return "<unparsable: $Json>"
    }
}

function Get-BasicCredential {
    param([string] $HeaderValue)
    if (-not $HeaderValue -or -not $HeaderValue.StartsWith('Basic ', [StringComparison]::OrdinalIgnoreCase)) {
        return $null
    }
    try {
        return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($HeaderValue.Substring(6).Trim()))
    }
    catch {
        return $null
    }
}

function Start-ContractMock {
    param([string] $Scenario, [string] $WorkDir)

    $logPath = Join-Path $WorkDir 'requests.jsonl'
    $portPath = Join-Path $WorkDir 'port'
    $certPath = Join-Path $WorkDir 'mock-cert.pem'
    $keyPath = Join-Path $WorkDir 'mock-key.pem'
    Remove-Item -LiteralPath $portPath -ErrorAction SilentlyContinue

    & $CertScript -CertificatePath $certPath -KeyPath $keyPath

    $arguments = @(
        $MockScript,
        '--contract', $ContractPath,
        '--cert', $certPath,
        '--key', $keyPath,
        '--log', $logPath,
        '--port-file', $portPath,
        '--scenario', $Scenario
    )
    $process = Start-Process -FilePath 'python3' -ArgumentList $arguments -PassThru `
        -RedirectStandardError (Join-Path $WorkDir 'mock.err') `
        -RedirectStandardOutput (Join-Path $WorkDir 'mock.out')

    $deadline = [datetime]::UtcNow.AddSeconds(30)
    while (-not (Test-Path -LiteralPath $portPath)) {
        if ($process.HasExited -or [datetime]::UtcNow -gt $deadline) {
            $stderr = if (Test-Path (Join-Path $WorkDir 'mock.err')) { Get-Content -Raw (Join-Path $WorkDir 'mock.err') } else { '' }
            throw "contract mock did not start. $stderr"
        }
        Start-Sleep -Milliseconds 100
    }

    return [pscustomobject]@{
        Process = $process
        Port    = [int](Get-Content -LiteralPath $portPath -Raw).Trim()
        LogPath = $logPath
    }
}

function Stop-ContractMock {
    param($Mock)
    if ($Mock -and -not $Mock.Process.HasExited) {
        Stop-Process -Id $Mock.Process.Id -Force -ErrorAction SilentlyContinue
        $Mock.Process.WaitForExit(5000) | Out-Null
    }
}

function Invoke-Scenario {
    param([string] $Scenario, [hashtable] $Overrides = @{})

    $workDir = Join-Path ([IO.Path]::GetTempPath()) ("vcf-rotation-{0}-{1}" -f $Scenario, [guid]::NewGuid().ToString('n').Substring(0, 8))
    New-Item -ItemType Directory -Path $workDir -Force | Out-Null
    $mock = $null
    try {
        $mock = Start-ContractMock -Scenario $Scenario -WorkDir $workDir
        $parameters = @{
            Server                   = "127.0.0.1:$($mock.Port)"
            Credential               = [pscredential]::new($Account, (ConvertTo-SecureString $NewSecret -AsPlainText -Force))
            RetiringSessionId        = $RetiringSessionId
            DrainService             = $DrainService
            DrainTimeoutSeconds      = 20
            PollIntervalMilliseconds = 100
            SkipCertificateCheck     = $true
        }
        foreach ($key in $Overrides.Keys) { $parameters[$key] = $Overrides[$key] }

        $result = $null
        $failure = $null
        try {
            $result = Invoke-VcenterCredentialRotation @parameters
        }
        catch {
            $failure = $_
        }

        Stop-ContractMock -Mock $mock
        $mock = $null

        $entries = @()
        if (Test-Path -LiteralPath $workDir) {
            $logPath = Join-Path $workDir 'requests.jsonl'
            if (Test-Path -LiteralPath $logPath) {
                $entries = @(Get-Content -LiteralPath $logPath | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json -AsHashtable })
            }
        }
        return [pscustomobject]@{
            Result  = $result
            Failure = $failure
            Entries = $entries
        }
    }
    finally {
        Stop-ContractMock -Mock $mock
        Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Test-NoContractViolation {
    param([string] $Scenario, $Entries)

    $violations = @($Entries | Where-Object { $_.violation })
    $detail = ($violations | ForEach-Object {
            "#{0} {1} {2} -> {3}{4}" -f $_.seq, $_.method, $_.target, $_.violation,
            $(if ($_.ContainsKey('violation_detail')) { " ($($_.violation_detail))" } else { '' })
        }) -join '; '
    Test-That -Name "$Scenario/ every request matched a contracted operation and a legal wire shape" `
        -Condition ($violations.Count -eq 0) -Detail $detail

    $errors = @($Entries | Where-Object { [int]$_.status -ge 400 })
    $errorDetail = ($errors | ForEach-Object { "#{0} {1} {2} -> {3}" -f $_.seq, $_.method, $_.target, $_.status }) -join '; '
    Test-That -Name "$Scenario/ no request was rejected by the endpoint" `
        -Condition ($errors.Count -eq 0) -Detail $errorDetail
}

# --------------------------------------------------------------------- preflight

Write-Host 'Preflight'
if (-not (Get-Command python3 -ErrorAction SilentlyContinue)) {
    throw 'python3 is required to run the loopback contract mock.'
}

$contract = Get-Content -LiteralPath $ContractPath -Raw | ConvertFrom-Json
$sources = Get-Content -LiteralPath $SourcesPath -Raw | ConvertFrom-Json
$contractOperations = @($contract.operations.PSObject.Properties.Name | Sort-Object)
$expectedOperations = @('Cis.Session_create', 'Cis.Session_delete', 'Cis.Session_get', 'Cis.Tasks_get', 'Cis.Tasks_list')

Test-Equal -Name 'contract names exactly the five operations the rotation uses' `
    -Expected ($expectedOperations -join ',') -Actual ($contractOperations -join ',')
Test-Equal -Name 'sources pin the vcenter.yaml revision the contract was derived from' `
    -Expected '85151f6b1bb58f13b6ac0304bfec53904bea085f' -Actual $sources.specification.commit_sha
Test-Equal -Name 'sources pin the specification path' `
    -Expected 'specifications/vsphere/openapi/automation/vcenter.yaml' -Actual $sources.specification.path
Test-Equal -Name 'sources record every contracted operationId' `
    -Expected ($expectedOperations -join ',') -Actual (($sources.operations.operationId | Sort-Object) -join ',')

Import-Module $ModulePath -Force -ErrorAction Stop
Test-That -Name 'module exports Invoke-VcenterCredentialRotation' `
    -Condition ([bool](Get-Command Invoke-VcenterCredentialRotation -ErrorAction SilentlyContinue))

# --------------------------------------------------------------------- nominal

Write-Host ''
Write-Host 'Scenario: nominal - two requests are still in flight on the retiring session'
$nominal = Invoke-Scenario -Scenario 'nominal'
$entries = $nominal.Entries

Test-That -Name 'nominal/ rotation completed' -Condition ($null -eq $nominal.Failure) `
    -Detail $(if ($nominal.Failure) { $nominal.Failure.Exception.Message } else { '' })
Test-NoContractViolation -Scenario 'nominal' -Entries $entries

$byOperation = @{}
foreach ($op in $expectedOperations) { $byOperation[$op] = @($entries | Where-Object { $_.operationId -eq $op }) }

Test-Equal -Name 'nominal/ exactly one Cis.Session_create' -Expected 1 -Actual $byOperation['Cis.Session_create'].Count
Test-Equal -Name 'nominal/ exactly one Cis.Session_get' -Expected 1 -Actual $byOperation['Cis.Session_get'].Count
Test-Equal -Name 'nominal/ exactly one Cis.Tasks_list' -Expected 1 -Actual $byOperation['Cis.Tasks_list'].Count
Test-Equal -Name 'nominal/ exactly one Cis.Session_delete' -Expected 1 -Actual $byOperation['Cis.Session_delete'].Count

if ($byOperation['Cis.Session_create'].Count -eq 1) {
    $create = $byOperation['Cis.Session_create'][0]
    Test-Equal -Name 'nominal/ Cis.Session_create is the first request' -Expected 1 -Actual $create.seq
    Test-Equal -Name 'nominal/ Cis.Session_create addresses POST /api/session' -Expected 'POST /api/session' `
        -Actual ("{0} {1}" -f $create.method, $create.path)
    Test-Equal -Name 'nominal/ Cis.Session_create carries no query string' -Expected '' -Actual $create.query
    Test-Equal -Name 'nominal/ Cis.Session_create authenticates with the new secret over basic_auth' `
        -Expected ("{0}:{1}" -f $Account, $NewSecret) `
        -Actual (Get-BasicCredential -HeaderValue $create.headers['authorization'])
    Test-That -Name 'nominal/ Cis.Session_create does not also send vmware-api-session-id' `
        -Condition (-not $create.headers.ContainsKey('vmware-api-session-id'))
    Test-Equal -Name 'nominal/ Cis.Session_create sends no request body' -Expected '' -Actual $create.body
}

if ($byOperation['Cis.Session_get'].Count -eq 1) {
    $get = $byOperation['Cis.Session_get'][0]
    Test-Equal -Name 'nominal/ Cis.Session_get validates the freshly minted session' `
        -Expected $RotatedSessionId -Actual $get.headers['vmware-api-session-id']
    Test-That -Name 'nominal/ Cis.Session_get does not also send Authorization' `
        -Condition (-not $get.headers.ContainsKey('authorization'))
    Test-Equal -Name 'nominal/ Cis.Session_get carries no query string' -Expected '' -Actual $get.query
}

if ($byOperation['Cis.Tasks_list'].Count -eq 1) {
    $list = $byOperation['Cis.Tasks_list'][0]
    Test-Equal -Name 'nominal/ Cis.Tasks_list addresses POST /api/cis/tasks?action=list' `
        -Expected 'POST /api/cis/tasks action=list' `
        -Actual ("{0} {1} {2}" -f $list.method, $list.path, $list.query)
    Test-Equal -Name 'nominal/ Cis.Tasks_list runs on the retiring session' `
        -Expected $RetiringSessionId -Actual $list.headers['vmware-api-session-id']
    $expectedBody = ConvertTo-CanonicalText -Json (@'
{"filter_spec":{"services":["com.vmware.vcenter.vm"],"status":["PENDING","RUNNING","BLOCKED"],"users":["svc-automation@vsphere.local"]}}
'@)
    Test-Equal -Name 'nominal/ Cis.Tasks_list body carries only the filter properties that were set' `
        -Expected $expectedBody -Actual (ConvertTo-CanonicalText -Json $list.body)
}

$polls = @($byOperation['Cis.Tasks_get'])
Test-Equal -Name 'nominal/ every in-flight task was polled to a terminal state' -Expected 5 -Actual $polls.Count
$pollsByTask = @($polls | Group-Object { ($_.path -split '/')[-1] } | Sort-Object Name)
Test-Equal -Name 'nominal/ polling covered exactly the tasks the list returned' `
    -Expected 'task-9001,task-9002' -Actual (@($pollsByTask | ForEach-Object { $_.Name }) -join ',')
foreach ($group in $pollsByTask) {
    $expectedPolls = if ($group.Name -eq 'task-9001') { 2 } else { 3 }
    Test-Equal -Name ("nominal/ {0} was polled until it settled and no further" -f $group.Name) `
        -Expected $expectedPolls -Actual $group.Count
}
foreach ($poll in $polls) {
    Test-Equal -Name ("nominal/ Cis.Tasks_get #{0} sends only the GetSpec properties that were set" -f $poll.seq) `
        -Expected 'exclude_result=true' -Actual $poll.query
    Test-Equal -Name ("nominal/ Cis.Tasks_get #{0} runs on the retiring session" -f $poll.seq) `
        -Expected $RetiringSessionId -Actual $poll.headers['vmware-api-session-id']
    Test-Equal -Name ("nominal/ Cis.Tasks_get #{0} sends no request body" -f $poll.seq) -Expected '' -Actual $poll.body
}

if ($byOperation['Cis.Session_delete'].Count -eq 1 -and $entries.Count -gt 0) {
    $delete = $byOperation['Cis.Session_delete'][0]
    Test-Equal -Name 'nominal/ the retiring session is deleted, not the new one' `
        -Expected $RetiringSessionId -Actual $delete.headers['vmware-api-session-id']
    $lastSeq = ($entries | ForEach-Object { [int]$_.seq } | Measure-Object -Maximum).Maximum
    Test-Equal -Name 'nominal/ Cis.Session_delete is the last request of the rotation' `
        -Expected $lastSeq -Actual $delete.seq
    $lastPoll = if ($polls.Count -gt 0) { ($polls | ForEach-Object { [int]$_.seq } | Measure-Object -Maximum).Maximum } else { 0 }
    Test-That -Name 'nominal/ the retiring session outlives the work still bound to it' `
        -Condition ($polls.Count -gt 0 -and [int]$delete.seq -gt $lastPoll) `
        -Detail ("delete at #{0}, last drain poll at #{1}" -f $delete.seq, $lastPoll)
}

# --------------------------------------------------------------------- idle

Write-Host ''
Write-Host 'Scenario: idle - nothing is in flight'
$idle = Invoke-Scenario -Scenario 'idle'
Test-That -Name 'idle/ rotation completed' -Condition ($null -eq $idle.Failure) `
    -Detail $(if ($idle.Failure) { $idle.Failure.Exception.Message } else { '' })
Test-NoContractViolation -Scenario 'idle' -Entries $idle.Entries
Test-Equal -Name 'idle/ the rotation still enumerates in-flight work once' -Expected 1 `
    -Actual @($idle.Entries | Where-Object { $_.operationId -eq 'Cis.Tasks_list' }).Count
Test-Equal -Name 'idle/ no task is polled when the list comes back empty' -Expected 0 `
    -Actual @($idle.Entries | Where-Object { $_.operationId -eq 'Cis.Tasks_get' }).Count
Test-Equal -Name 'idle/ the retiring session is still deleted last' `
    -Expected 'Cis.Session_create,Cis.Session_get,Cis.Tasks_list,Cis.Session_delete' `
    -Actual (($idle.Entries | Sort-Object { [int]$_.seq } | ForEach-Object { $_.operationId }) -join ',')

# --------------------------------------------------------------------- stuck

Write-Host ''
Write-Host 'Scenario: stuck - an in-flight request never settles'
$stuck = Invoke-Scenario -Scenario 'stuck' -Overrides @{ DrainTimeoutSeconds = 0 }
Test-That -Name 'stuck/ the rotation reports failure instead of returning success' `
    -Condition ($null -ne $stuck.Failure)
Test-NoContractViolation -Scenario 'stuck' -Entries $stuck.Entries
Test-Equal -Name 'stuck/ the drain enumerated the retiring session before timing out' `
    -Expected 1 -Actual @($stuck.Entries | Where-Object { $_.operationId -eq 'Cis.Tasks_list' }).Count
Test-Equal -Name 'stuck/ the retiring session is left alive so the in-flight request is not stranded' `
    -Expected 0 -Actual @($stuck.Entries | Where-Object { $_.operationId -eq 'Cis.Session_delete' }).Count

# --------------------------------------------------------------- identity mismatch

Write-Host ''
Write-Host 'Scenario: identity-mismatch - the new session belongs to a different principal'
$mismatch = Invoke-Scenario -Scenario 'identity-mismatch'
Test-That -Name 'identity-mismatch/ the rotation reports failure instead of returning success' `
    -Condition ($null -ne $mismatch.Failure)
Test-NoContractViolation -Scenario 'identity-mismatch' -Entries $mismatch.Entries
Test-Equal -Name 'identity-mismatch/ the retiring session is not deleted' `
    -Expected 0 -Actual @($mismatch.Entries | Where-Object { $_.operationId -eq 'Cis.Session_delete' }).Count

# --------------------------------------------------------------------- summary

Write-Host ''
Write-Host ("{0} checks, {1} failed" -f $script:Checks, $script:Failures.Count)
if ($script:Failures.Count -gt 0) {
    Write-Host 'FAILED' -ForegroundColor Red
    exit 1
}
Write-Host 'OK'
exit 0
