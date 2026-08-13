[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [int] $Port,

    [Parameter(Mandatory)]
    [string] $ConfigPath,

    [Parameter(Mandatory)]
    [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$FilesRoot = Split-Path -Parent $PSScriptRoot
$ModulePath = Join-Path (
    Join-Path $FilesRoot 'VcfOpsNetworksApp'
) 'VcfOpsNetworksApp.psm1'
$Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json -AsHashtable

# Import the implementation file directly so author verification remains
# possible when the task runner, rather than this shell, provisions the
# protected manifest's external VCF PowerCLI prerequisite.
Import-Module $ModulePath -Force -ErrorAction Stop

$Password = ConvertTo-SecureString $Config.password -AsPlainText -Force
$Credential = [pscredential]::new($Config.username, $Password)

$Common = @{
    Server     = '127.0.0.1'
    Protocol   = 'http'
    Port       = $Port
    Credential = $Credential
    PageSize   = $Config.page_size
}

$TiersA = @(
    @{
        Name     = $Config.tier_a_one_name
        VmFilter = $Config.tier_a_one_filter
    },
    @{
        Name      = $Config.tier_a_two_name
        IpAddress = [string[]] $Config.tier_a_two_addresses
    }
)

$TiersB = @(
    @{
        Name      = $Config.tier_b_one_name
        VmFilter  = $Config.tier_b_one_filter
        IpAddress = [string[]] $Config.tier_b_one_addresses
    }
)

function Test-RejectedBeforeTraffic {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string] $Label,

        [Parameter(Mandatory)]
        [scriptblock] $Action
    )

    try {
        & $Action
    }
    catch [System.ArgumentException] {
        return $true
    }
    catch {
        throw "$Label raised $($_.Exception.GetType().FullName), not ArgumentException."
    }
    throw "$Label was not rejected before traffic."
}

$ValidationResults = [ordered] @{}
$ValidationResults['BlankServer'] = Test-RejectedBeforeTraffic 'blank Server' {
    New-VcfOpsNetworksApplication @Common -Server '' `
        -Name $Config.application_a_name -Tier $TiersA
}
$ValidationResults['PaddedServer'] = Test-RejectedBeforeTraffic 'padded Server' {
    New-VcfOpsNetworksApplication @Common -Server ' 127.0.0.1 ' `
        -Name $Config.application_a_name -Tier $TiersA
}
$ValidationResults['BlankName'] = Test-RejectedBeforeTraffic 'blank Name' {
    New-VcfOpsNetworksApplication @Common -Name '' -Tier $TiersA
}
$ValidationResults['PaddedName'] = Test-RejectedBeforeTraffic 'padded Name' {
    New-VcfOpsNetworksApplication @Common `
        -Name "  $($Config.application_a_name)  " -Tier $TiersA
}
$ValidationResults['EmptyTiers'] = Test-RejectedBeforeTraffic 'empty Tier array' {
    New-VcfOpsNetworksApplication @Common `
        -Name $Config.application_a_name -Tier @()
}
$ValidationResults['BlankTierName'] = Test-RejectedBeforeTraffic 'blank tier Name' {
    New-VcfOpsNetworksApplication @Common -Name $Config.application_a_name `
        -Tier @(@{ Name = ' '; VmFilter = $Config.tier_a_one_filter })
}
$ValidationResults['PaddedTierName'] = Test-RejectedBeforeTraffic 'padded tier Name' {
    New-VcfOpsNetworksApplication @Common -Name $Config.application_a_name `
        -Tier @(@{ Name = ' padded '; VmFilter = $Config.tier_a_one_filter })
}
$ValidationResults['RepeatedTierName'] = Test-RejectedBeforeTraffic 'repeated tier Name' {
    New-VcfOpsNetworksApplication @Common -Name $Config.application_a_name -Tier @(
        @{ Name = 'same'; VmFilter = $Config.tier_a_one_filter },
        @{ Name = 'same'; IpAddress = [string[]] @('10.1.1.1') }
    )
}
$ValidationResults['MissingLdapValue'] = Test-RejectedBeforeTraffic 'missing LDAP DomainValue' {
    New-VcfOpsNetworksApplication @Common -Name $Config.application_a_name `
        -Tier $TiersA -DomainType 'LDAP'
}
$ValidationResults['UnknownTierKey'] = Test-RejectedBeforeTraffic 'unknown tier key' {
    New-VcfOpsNetworksApplication @Common -Name $Config.application_a_name `
        -Tier @(@{
                Name = 'bad-key'
                VmFilter = $Config.tier_a_one_filter
                Other = 'not in the contract'
            })
}
$ValidationResults['MissingCriteria'] = Test-RejectedBeforeTraffic 'missing tier criteria' {
    New-VcfOpsNetworksApplication @Common -Name $Config.application_a_name `
        -Tier @(@{ Name = 'no-criteria' })
}

$First = New-VcfOpsNetworksApplication @Common `
    -Name $Config.application_a_name `
    -Tier $TiersA

# The same call again. The node accepts duplicate names, so a second
# application appearing here would be a real duplicate.
$Second = New-VcfOpsNetworksApplication @Common `
    -Name $Config.application_a_name `
    -Tier $TiersA `
    -DomainValue $Config.ignored_local_domain_value

$Third = New-VcfOpsNetworksApplication @Common `
    -Name $Config.application_b_name `
    -Tier $TiersB `
    -DomainType 'LDAP' `
    -DomainValue $Config.domain_value `
    -EnableIntent $false `
    -LastModifiedTimestamp $Config.last_modified_timestamp

# This exact name is on the first page. A compliant probe stops immediately
# even though that page also carries a cursor.
$EarlyExisting = New-VcfOpsNetworksApplication @Common `
    -Name $Config.early_existing_name `
    -Tier $TiersA

# The fixture fails this run's first probe. The function must still delete the
# token in its finally path, then surface the earlier failure.
$FailureObserved = $false
try {
    $null = New-VcfOpsNetworksApplication @Common `
        -Name $Config.failure_application_name `
        -Tier $TiersA
}
catch {
    $FailureObserved = $true
}
if (-not $FailureObserved) {
    throw 'The fixture failure was not surfaced.'
}

[pscustomobject] @{
    ValidationResults = $ValidationResults
    First             = $First
    Second            = $Second
    Third             = $Third
    EarlyExisting     = $EarlyExisting
    FailureObserved   = $FailureObserved
} |
    ConvertTo-Json -Depth 20 -Compress |
    Set-Content -LiteralPath $OutputPath -Encoding utf8NoBOM -NoNewline
