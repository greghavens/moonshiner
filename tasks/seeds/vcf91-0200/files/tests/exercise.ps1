[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [int] $Port,

    [Parameter(Mandatory)]
    [string] $OutputFile
)

$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
$manifest = Join-Path $root 'src/VcfInstaller.Depot/VcfInstaller.Depot.psd1'
$sdkVersion = [version] '13.5.0.25380678'

$installed = Get-Module -ListAvailable -Name VMware.Sdk.Vcf.Installer |
    Where-Object Version -EQ $sdkVersion |
    Select-Object -First 1
if ($null -eq $installed) {
    throw 'Required environment prerequisite VMware.Sdk.Vcf.Installer 13.5.0.25380678 is not installed.'
}

Import-Module $manifest -Force

foreach ($sdkCommandName in @(
    'Initialize-VcfInstallerDepotAccount',
    'Initialize-VcfInstallerDepotSettings',
    'Invoke-VcfInstallerUpdateDepotSettings'
)) {
    $sdkCommand = Get-Command $sdkCommandName -CommandType Cmdlet -ErrorAction Stop
    if ($sdkCommand.Source -cne 'VMware.Sdk.Vcf.Installer') {
        throw "$sdkCommandName did not resolve to the genuine Installer SDK module."
    }
}

$candidate = Get-Command Set-VcfInstallerDepotToken `
    -CommandType Function -ErrorAction Stop
$candidateExports = @(Get-Command -Module $candidate.ModuleName -CommandType Function)
if ($candidateExports.Count -ne 1 -or
    $candidateExports[0].Name -cne 'Set-VcfInstallerDepotToken') {
    throw 'The candidate module must export exactly Set-VcfInstallerDepotToken.'
}

$expectedTypes = [ordered]@{
    Server = [object]
    DownloadToken = [string]
    DownloadActivationCode = [string]
    RetryCount = [int]
    RetryDelaySeconds = [int]
}
foreach ($entry in $expectedTypes.GetEnumerator()) {
    $parameter = $candidate.Parameters[$entry.Key]
    if ($null -eq $parameter -or $parameter.ParameterType -ne $entry.Value) {
        throw "Public parameter $($entry.Key) is missing or has the wrong type."
    }
    $mandatory = @(
        $parameter.Attributes |
            Where-Object { $_ -is [System.Management.Automation.ParameterAttribute] }
    )[0].Mandatory
    $shouldBeMandatory = $entry.Key -in @('Server', 'DownloadToken')
    if ($mandatory -ne $shouldBeMandatory) {
        throw "Public parameter $($entry.Key) has the wrong Mandatory contract."
    }
}

$tokenLength = @(
    $candidate.Parameters.DownloadToken.Attributes |
        Where-Object { $_ -is [System.Management.Automation.ValidateLengthAttribute] }
)
$retryRange = @(
    $candidate.Parameters.RetryCount.Attributes |
        Where-Object { $_ -is [System.Management.Automation.ValidateRangeAttribute] }
)
$delayRange = @(
    $candidate.Parameters.RetryDelaySeconds.Attributes |
        Where-Object { $_ -is [System.Management.Automation.ValidateRangeAttribute] }
)
if ($tokenLength.Count -ne 1 -or
    [int] $tokenLength[0].MinLength -ne 1 -or
    [int] $tokenLength[0].MaxLength -ne 32) {
    throw 'DownloadToken must retain ValidateLength(1, 32).'
}
if ($retryRange.Count -ne 1 -or
    [int] $retryRange[0].MinRange -ne 0 -or
    [int] $retryRange[0].MaxRange -ne 10) {
    throw 'RetryCount must retain ValidateRange(0, 10).'
}
if ($delayRange.Count -ne 1 -or
    [int] $delayRange[0].MinRange -ne 0 -or
    [int] $delayRange[0].MaxRange -ne 300) {
    throw 'RetryDelaySeconds must retain ValidateRange(0, 300).'
}

$defaultText = @{}
foreach ($parameterAst in $candidate.ScriptBlock.Ast.Body.ParamBlock.Parameters) {
    $name = $parameterAst.Name.VariablePath.UserPath
    $defaultText[$name] = if ($null -eq $parameterAst.DefaultValue) {
        ''
    }
    else {
        $parameterAst.DefaultValue.Extent.Text
    }
}
if ($defaultText.RetryCount -cne '2' -or
    $defaultText.RetryDelaySeconds -cne '1') {
    throw 'Retry defaults must remain two retries and one second.'
}

$candidateCommandNames = @(
    $candidate.ScriptBlock.Ast.FindAll(
        {
            param($node)
            $node -is [System.Management.Automation.Language.CommandAst]
        },
        $true
    ) |
        ForEach-Object { $_.GetCommandName() } |
        Where-Object { $null -ne $_ }
)
foreach ($requiredCommandName in @(
    'Initialize-VcfInstallerDepotAccount',
    'Initialize-VcfInstallerDepotSettings',
    'Invoke-VcfInstallerUpdateDepotSettings'
)) {
    if ($requiredCommandName -cnotin $candidateCommandNames) {
        throw "The implementation must invoke $requiredCommandName directly."
    }
}

$connectCommand = Get-Command Connect-VcfInstallerServer -ErrorAction Stop
$connect = @{
    User = 'moonshiner-verifier'
}
$plainPassword = 'local-loopback-password'
if ($connectCommand.Parameters.Password.ParameterType -eq [securestring]) {
    $connect.Password = ConvertTo-SecureString $plainPassword -AsPlainText -Force
}
else {
    $connect.Password = $plainPassword
}
if ($connectCommand.Parameters.ContainsKey('Port')) {
    $connect.Port = $Port
}
if ($connectCommand.Parameters.ContainsKey('Protocol')) {
    $connect.Server = '127.0.0.1'
    $connect.Protocol = 'http'
}
else {
    $connect.Server = "http://127.0.0.1:$Port"
}
if ($connectCommand.Parameters.ContainsKey('Force')) {
    $connect.Force = $true
}
if ($connectCommand.Parameters.ContainsKey('NotDefault')) {
    $connect.NotDefault = $true
}
$serverConnection = Connect-VcfInstallerServer @connect

$primaryToken = '0123456789abcdef0123456789abcdef'
$firstItems = @(Set-VcfInstallerDepotToken `
    -Server $serverConnection `
    -DownloadToken $primaryToken `
    -RetryCount 2 `
    -RetryDelaySeconds 0)
if ($firstItems.Count -ne 1) {
    throw "Expected one response from the retried update, received $($firstItems.Count)."
}

$repeatItems = @(Set-VcfInstallerDepotToken `
    -Server $serverConnection `
    -DownloadToken $primaryToken `
    -RetryCount 0 `
    -RetryDelaySeconds 0)
if ($repeatItems.Count -ne 1) {
    throw "Expected one response from the repeated update, received $($repeatItems.Count)."
}

try {
    $null = Set-VcfInstallerDepotToken `
        -Server $serverConnection `
        -DownloadToken 'fedcba9876543210fedcba9876543210' `
        -DownloadActivationCode 'activation-code-01' `
        -RetryCount 3 `
        -RetryDelaySeconds 0
    $clientErrorRejected = $false
}
catch {
    $clientErrorRejected = $true
}
if (-not $clientErrorRejected) {
    throw 'The non-retryable HTTP 400 response was accepted.'
}

$transientCases = @(
    [ordered]@{
        Name = 'request timeout'
        Token = '11111111111111111111111111111111'
    },
    [ordered]@{
        Name = 'throttling'
        Token = '22222222222222222222222222222222'
    },
    [ordered]@{
        Name = 'server error'
        Token = '33333333333333333333333333333333'
    },
    [ordered]@{
        Name = 'ambiguous transport failure'
        Token = '44444444444444444444444444444444'
    }
)
foreach ($transientCase in $transientCases) {
    $items = @(Set-VcfInstallerDepotToken `
        -Server $serverConnection `
        -DownloadToken $transientCase.Token `
        -RetryCount 1 `
        -RetryDelaySeconds 0)
    if ($items.Count -ne 1 -or
        [string] $items[0].VmwareAccount.DownloadToken -cne $transientCase.Token) {
        throw "The $($transientCase.Name) retry did not return its SDK response."
    }
}

$exhaustedStatus = $null
$exhaustedType = $null
try {
    $null = Set-VcfInstallerDepotToken `
        -Server $serverConnection `
        -DownloadToken '55555555555555555555555555555555' `
        -RetryCount 2 `
        -RetryDelaySeconds 0
    $retryExhausted = $false
}
catch {
    $retryExhausted = $true
    $exception = $_.Exception
    while ($null -ne $exception) {
        if ($null -eq $exhaustedType) {
            $exhaustedType = $exception.GetType().FullName
        }
        foreach ($propertyName in @('StatusCode', 'ErrorCode')) {
            $property = $exception.PSObject.Properties[$propertyName]
            if ($null -ne $property -and $null -ne $property.Value) {
                try {
                    $candidateStatus = [int] $property.Value
                    if ($candidateStatus -ge 100 -and $candidateStatus -le 599) {
                        $exhaustedStatus = $candidateStatus
                        break
                    }
                }
                catch {
                    # Keep walking the genuine SDK exception chain.
                }
            }
        }
        if ($null -ne $exhaustedStatus) {
            break
        }
        $exception = $exception.InnerException
    }
}
if (-not $retryExhausted -or $exhaustedStatus -ne 503) {
    throw 'Retry exhaustion did not rethrow the final HTTP 503 SDK error.'
}

[ordered]@{
    firstDownloadToken = [string] $firstItems[0].VmwareAccount.DownloadToken
    repeatedDownloadToken = [string] $repeatItems[0].VmwareAccount.DownloadToken
    clientErrorRejected = $clientErrorRejected
    transientCasesRetried = $transientCases.Count
    exhaustedStatus = $exhaustedStatus
    exhaustedType = $exhaustedType
} | ConvertTo-Json -Depth 4 -Compress |
    Set-Content -LiteralPath $OutputFile -Encoding utf8NoBOM
