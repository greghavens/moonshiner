[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [int] $Port,

    [Parameter(Mandatory)]
    [string] $ConfigPath,

    [Parameter(Mandatory)]
    [string] $RequestLogPath,

    [Parameter(Mandatory)]
    [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$FilesRoot = Split-Path -Parent $PSScriptRoot
$ModulePath = Join-Path (
    Join-Path $FilesRoot 'VcfOpsLogCredential'
) 'VcfOpsLogCredential.psm1'
$Config = Get-Content -LiteralPath $ConfigPath -Raw |
    ConvertFrom-Json -AsHashtable

# The task runner provisions the genuine prerequisite declared by the protected
# manifest. Directly importing the implementation keeps this authoring verifier
# usable in a minimal shell without copying or imitating that external module.
Import-Module $ModulePath -Force -ErrorAction Stop

function Test-VcfOpsLogClientDisposed {
    param(
        [Parameter(Mandatory)]
        [Net.Http.HttpClient] $Client
    )

    try {
        $Client.CancelPendingRequests()
        return $false
    }
    catch [ObjectDisposedException] {
        return $true
    }
}

if (
    $Config.ContainsKey('validation_only') -and
    $Config.validation_only
) {
    $script:ValidationGate = New-VcfOpsLogCredentialGate `
        -SecretName $Config.old_name `
        -Secret $Config.old_secret `
        -AccessToken $Config.old_access_token
    $ValidationHandler = [Net.Http.HttpClientHandler]::new()
    $ValidationHandler.AllowAutoRedirect = $false
    $ValidationClient = [Net.Http.HttpClient]::new(
        $ValidationHandler,
        $false
    )
    $script:ValidationClient = $ValidationClient
    $script:ValidationOrigin = [uri] "http://127.0.0.1:${Port}/"
    $script:ValidationUnexpected = [Collections.Generic.List[string]]::new()

    function Invoke-ValidationRotation {
        param(
            [Parameter(Mandatory)]
            [object] $CandidateBaseUri,

            [Parameter(Mandatory)]
            [AllowEmptyString()]
            [string] $CandidateLogToken,

            [Parameter(Mandatory)]
            [AllowEmptyString()]
            [string] $CandidateNewName
        )

        Invoke-VcfOpsLogCredentialRotation `
            -Gate $script:ValidationGate `
            -BaseUri $CandidateBaseUri `
            -LogToken $CandidateLogToken `
            -NewName $CandidateNewName `
            -MaxDrainChecks 1 `
            -DrainPollIntervalMilliseconds 0 `
            -SleepAction {} `
            -HttpClient $script:ValidationClient
    }

    $ValidationAttempts = @(
        @{
            Label = 'blank initial name'
            Action = {
                New-VcfOpsLogCredentialGate `
                    -SecretName ' ' `
                    -Secret $Config.old_secret `
                    -AccessToken $Config.old_access_token
            }
        }
        @{
            Label = 'initial name whitespace'
            Action = {
                New-VcfOpsLogCredentialGate `
                    -SecretName " $($Config.old_name)" `
                    -Secret $Config.old_secret `
                    -AccessToken $Config.old_access_token
            }
        }
        @{
            Label = 'blank initial secret'
            Action = {
                New-VcfOpsLogCredentialGate `
                    -SecretName $Config.old_name `
                    -Secret ' ' `
                    -AccessToken $Config.old_access_token
            }
        }
        @{
            Label = 'blank initial access token'
            Action = {
                New-VcfOpsLogCredentialGate `
                    -SecretName $Config.old_name `
                    -Secret $Config.old_secret `
                    -AccessToken ' '
            }
        }
        @{
            Label = 'blank new name'
            Action = {
                Invoke-ValidationRotation `
                    -CandidateBaseUri $script:ValidationOrigin `
                    -CandidateLogToken $Config.log_token `
                    -CandidateNewName ' '
            }
        }
        @{
            Label = 'new name whitespace'
            Action = {
                Invoke-ValidationRotation `
                    -CandidateBaseUri $script:ValidationOrigin `
                    -CandidateLogToken $Config.log_token `
                    -CandidateNewName "$($Config.new_name) "
            }
        }
        @{
            Label = 'blank log token'
            Action = {
                Invoke-ValidationRotation `
                    -CandidateBaseUri $script:ValidationOrigin `
                    -CandidateLogToken ' ' `
                    -CandidateNewName $Config.new_name
            }
        }
        @{
            Label = 'header-unsafe log token'
            Action = {
                Invoke-ValidationRotation `
                    -CandidateBaseUri $script:ValidationOrigin `
                    -CandidateLogToken "bad`r`ntoken" `
                    -CandidateNewName $Config.new_name
            }
        }
        @{
            Label = 'unchanged new name'
            Action = {
                Invoke-ValidationRotation `
                    -CandidateBaseUri $script:ValidationOrigin `
                    -CandidateLogToken $Config.log_token `
                    -CandidateNewName $Config.old_name
            }
        }
        @{
            Label = 'relative base URI'
            Action = {
                Invoke-ValidationRotation `
                    -CandidateBaseUri 'relative/path' `
                    -CandidateLogToken $Config.log_token `
                    -CandidateNewName $Config.new_name
            }
        }
        @{
            Label = 'non-HTTP base URI'
            Action = {
                Invoke-ValidationRotation `
                    -CandidateBaseUri 'ftp://127.0.0.1/' `
                    -CandidateLogToken $Config.log_token `
                    -CandidateNewName $Config.new_name
            }
        }
        @{
            Label = 'credentialed base URI'
            Action = {
                Invoke-ValidationRotation `
                    -CandidateBaseUri "http://user@127.0.0.1:${Port}/" `
                    -CandidateLogToken $Config.log_token `
                    -CandidateNewName $Config.new_name
            }
        }
        @{
            Label = 'base URI query'
            Action = {
                Invoke-ValidationRotation `
                    -CandidateBaseUri "http://127.0.0.1:${Port}/?x=1" `
                    -CandidateLogToken $Config.log_token `
                    -CandidateNewName $Config.new_name
            }
        }
        @{
            Label = 'base URI fragment'
            Action = {
                Invoke-ValidationRotation `
                    -CandidateBaseUri "http://127.0.0.1:${Port}/#x" `
                    -CandidateLogToken $Config.log_token `
                    -CandidateNewName $Config.new_name
            }
        }
        @{
            Label = 'base URI non-root path'
            Action = {
                Invoke-ValidationRotation `
                    -CandidateBaseUri "http://127.0.0.1:${Port}/logs" `
                    -CandidateLogToken $Config.log_token `
                    -CandidateNewName $Config.new_name
            }
        }
    )

    try {
        foreach ($Attempt in $ValidationAttempts) {
            try {
                $null = & $Attempt.Action
                $script:ValidationUnexpected.Add([string] $Attempt.Label)
            }
            catch {
                # Each validation attempt is expected to terminate locally.
            }
        }
        $RequestCount = @(
            Get-Content -LiteralPath $RequestLogPath |
                Where-Object { $_.Length -gt 0 }
        ).Count
        $Output = [ordered] @{
            CaseStatus = 'validation'
            ExpectedFailureCount = $ValidationAttempts.Count
            UnexpectedSuccesses = [string[]] $script:ValidationUnexpected.ToArray()
            RequestCount = $RequestCount
            CallerClientDisposed = (
                Test-VcfOpsLogClientDisposed -Client $ValidationClient
            )
        }
        $Output |
            ConvertTo-Json -Depth 30 -Compress |
            Set-Content `
                -LiteralPath $OutputPath `
                -Encoding utf8NoBOM `
                -NoNewline
    }
    finally {
        $ValidationClient.Dispose()
        $ValidationHandler.Dispose()
    }
    exit 0
}

$Gate = New-VcfOpsLogCredentialGate `
    -SecretName $Config.old_name `
    -Secret $Config.old_secret `
    -AccessToken $Config.old_access_token

$script:Gate = $Gate
$script:OldLease = $null
$script:SleepCalls = 0
$script:SleepArguments = [Collections.Generic.List[int]]::new()
$script:BeforeReleaseOperations = @()
$script:OldDuringCutover = $null
$script:NewDuringCutover = $null
$script:OwnershipErrorType = $null

if ($Config.hold_old_lease) {
    $script:OldLease = Get-VcfOpsLogCredentialLease -Gate $Gate
}

$SleepAction = {
    param([int] $Milliseconds)

    $script:SleepCalls++
    $script:SleepArguments.Add($Milliseconds)
    if (
        $Config.ContainsKey('test_gate_ownership') -and
        $Config.test_gate_ownership
    ) {
        try {
            $null = Invoke-VcfOpsLogCredentialRotation `
                -Gate $script:Gate `
                -BaseUri ([uri] "http://127.0.0.1:${Port}/") `
                -LogToken $Config.log_token `
                -NewName "$($Config.new_name)-nested" `
                -MaxDrainChecks 1 `
                -DrainPollIntervalMilliseconds 0 `
                -SleepAction {} `
                -HttpClient $HttpClient
            $script:OwnershipErrorType = 'NoError'
        }
        catch {
            $script:OwnershipErrorType = $_.Exception.GetType().Name
        }
    }
    $script:BeforeReleaseOperations = @(
        Get-Content -LiteralPath $RequestLogPath |
            Where-Object { $_.Length -gt 0 } |
            ForEach-Object {
                ($_ | ConvertFrom-Json).operationId
            }
    )

    $script:OldDuringCutover = [ordered] @{
        SecretName = $script:OldLease.SecretName
        Secret = $script:OldLease.Secret
        AccessToken = $script:OldLease.AccessToken
    }
    $NewLease = Get-VcfOpsLogCredentialLease -Gate $script:Gate
    try {
        $script:NewDuringCutover = [ordered] @{
            SecretName = $NewLease.SecretName
            Secret = $NewLease.Secret
            AccessToken = $NewLease.AccessToken
        }
    }
    finally {
        $NewLease.Dispose()
    }
    $script:OldLease.Dispose()
}

$Handler = [Net.Http.HttpClientHandler]::new()
$Handler.AllowAutoRedirect = $false
$HttpClient = [Net.Http.HttpClient]::new($Handler, $false)
$SupplyHttpClient = (
    -not $Config.ContainsKey('supply_http_client') -or
    [bool] $Config.supply_http_client
)
try {
    $Parameters = @{
        Gate = $Gate
        BaseUri = [uri] "http://127.0.0.1:${Port}/"
        LogToken = $Config.log_token
        NewName = $Config.new_name
        MaxDrainChecks = $Config.max_drain_checks
        DrainPollIntervalMilliseconds = $Config.drain_interval
        SleepAction = $SleepAction
    }
    if ($SupplyHttpClient) {
        $Parameters.HttpClient = $HttpClient
    }
    if ($Config.bind_ttl) {
        $Parameters.SessionTtlMilliseconds = [long] $Config.request_ttl
    }

    try {
        $Result = Invoke-VcfOpsLogCredentialRotation @Parameters
        $CallerClientDisposed = if ($SupplyHttpClient) {
            Test-VcfOpsLogClientDisposed -Client $HttpClient
        } else {
            $null
        }
        $FinalLease = Get-VcfOpsLogCredentialLease -Gate $Gate
        try {
            $FinalValues = [ordered] @{
                SecretName = $FinalLease.SecretName
                Secret = $FinalLease.Secret
                AccessToken = $FinalLease.AccessToken
            }
        }
        finally {
            $FinalLease.Dispose()
        }

        $Output = [ordered] @{
            CaseStatus = 'success'
            Result = $Result
            SleepCalls = $script:SleepCalls
            SleepArguments = [int[]] $script:SleepArguments.ToArray()
            BeforeReleaseOperations = $script:BeforeReleaseOperations
            OldDuringCutover = $script:OldDuringCutover
            NewDuringCutover = $script:NewDuringCutover
            OwnershipErrorType = $script:OwnershipErrorType
            CallerClientDisposed = $CallerClientDisposed
            FinalValues = $FinalValues
        }
    }
    catch {
        $Exception = $_.Exception
        $CallerClientDisposed = if ($SupplyHttpClient) {
            Test-VcfOpsLogClientDisposed -Client $HttpClient
        } else {
            $null
        }
        $OldNameValue = if ($Exception.PSObject.Properties['OldName']) {
            $Exception.OldName
        } else {
            $null
        }
        $NewNameValue = if ($Exception.PSObject.Properties['NewName']) {
            $Exception.NewName
        } else {
            $null
        }
        $DrainCheckCountValue = if (
            $Exception.PSObject.Properties['DrainCheckCount']
        ) {
            $Exception.DrainCheckCount
        } else {
            $null
        }
        $PostFailureLease = Get-VcfOpsLogCredentialLease -Gate $Gate
        try {
            $PostFailureValues = [ordered] @{
                SecretName = $PostFailureLease.SecretName
                Secret = $PostFailureLease.Secret
                AccessToken = $PostFailureLease.AccessToken
            }
        }
        finally {
            $PostFailureLease.Dispose()
        }

        $Output = [ordered] @{
            CaseStatus = 'error'
            ErrorType = $Exception.GetType().Name
            ErrorMessage = $Exception.Message
            OldName = $OldNameValue
            NewName = $NewNameValue
            DrainCheckCount = $DrainCheckCountValue
            SleepCalls = $script:SleepCalls
            SleepArguments = [int[]] $script:SleepArguments.ToArray()
            OwnershipErrorType = $script:OwnershipErrorType
            CallerClientDisposed = $CallerClientDisposed
            PostFailureValues = $PostFailureValues
        }
    }

    $Output |
        ConvertTo-Json -Depth 30 -Compress |
        Set-Content `
            -LiteralPath $OutputPath `
            -Encoding utf8NoBOM `
            -NoNewline
}
finally {
    if ($null -ne $script:OldLease) {
        $script:OldLease.Dispose()
    }
    $HttpClient.Dispose()
    $Handler.Dispose()
}
