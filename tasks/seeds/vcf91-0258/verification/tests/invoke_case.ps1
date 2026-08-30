[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [int] $Port,

    [Parameter(Mandatory)]
    [string] $ConfigPath,

    [Parameter(Mandatory)]
    [string] $OutputPath,

    [switch] $ExpectInconclusive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$FilesRoot = Split-Path -Parent $PSScriptRoot
$ModulePath = Join-Path (
    Join-Path $FilesRoot 'VcfOpsCollectionTriage'
) 'VcfOpsCollectionTriage.psm1'
$Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json -AsHashtable

# Import the implementation file directly so author verification remains
# possible when the task runner, rather than this shell, provisions the
# protected manifest's external VCF PowerCLI prerequisite.
Import-Module $ModulePath -Force -ErrorAction Stop

$Password = ConvertTo-SecureString $Config.password -AsPlainText -Force
$Credential = [pscredential]::new($Config.username, $Password)

$Common = @{
    Server       = '127.0.0.1'
    Protocol     = 'http'
    Port         = $Port
    Credential   = $Credential
    ResourceKind = $Config.resource_kind
    AdapterKind  = $Config.adapter_kind
    AuthSource   = $Config.auth_source
    PageSize     = $Config.page_size
}

$ValidationValues = [ordered] @{
    Server       = @('', ' 127.0.0.1 ')
    ObjectName   = @('', " $($Config.object_name) ")
    ResourceKind = @('', " $($Config.resource_kind) ")
    AdapterKind  = @('', " $($Config.adapter_kind) ")
    AuthSource   = @('', " $($Config.auth_source) ")
}
foreach ($ParameterName in $ValidationValues.Keys) {
    foreach ($InvalidValue in $ValidationValues[$ParameterName]) {
        $Attempt = $Common.Clone()
        $Attempt['ObjectName'] = $Config.object_name
        $Attempt[$ParameterName] = $InvalidValue

        $RejectedBeforeTraffic = $false
        try {
            $null = Get-VcfOpsCollectionDiagnosis @Attempt
        }
        catch [System.ArgumentException] {
            $RejectedBeforeTraffic = $true
        }
        if (-not $RejectedBeforeTraffic) {
            throw "$ParameterName accepted a blank or padded value."
        }
    }
}

if ($ExpectInconclusive) {
    $Result = $null
    try {
        $null = Get-VcfOpsCollectionDiagnosis @Common -ObjectName $Config.object_name
    }
    catch {
        $Result = [pscustomobject] [ordered] @{
            Rejected = $true
        }
    }
    if ($null -eq $Result) {
        throw 'Inconclusive symptom evidence produced a verdict.'
    }
}
else {
    $Result = Get-VcfOpsCollectionDiagnosis @Common -ObjectName $Config.object_name
}

$Result |
    ConvertTo-Json -Depth 20 -Compress |
    Set-Content -LiteralPath $OutputPath -Encoding utf8NoBOM -NoNewline
