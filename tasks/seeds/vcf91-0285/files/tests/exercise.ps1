#Requires -Version 7.4
<#
    Protected harness for vcf91-0285.

    Drives the candidate VcfOpsNetworks module against the contract-pinned
    loopback mock and writes a structured result document. Assertions live in
    tests/verify.py; this script only records what happened.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)] [int]    $Port,
    [Parameter(Mandatory)] [string] $OutputFile,
    [Parameter(Mandatory)] [string] $ModulePath
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$USERNAME = 'admin@local'
$PASSWORD = 'VMware1!VMware1!'

function New-Cred {
    param([string] $User, [string] $Secret)
    [pscredential]::new($User, (ConvertTo-SecureString $Secret -AsPlainText -Force))
}

function Get-Prop {
    param($Object, [string] $Name)
    if ($null -eq $Object) { return $null }

    # The emitted objects may be deserialized JSON dictionaries or PSObjects.
    $dictionary = $Object -as [System.Collections.Generic.IDictionary[string, object]]
    if ($null -ne $dictionary) {
        if ($dictionary.ContainsKey($Name)) { return $dictionary[$Name] }
        return $null
    }

    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function ConvertTo-Record {
    param($Application)
    [ordered]@{
        entity_id    = [string](Get-Prop $Application 'entity_id')
        name         = [string](Get-Prop $Application 'name')
        entity_type  = [string](Get-Prop $Application 'entity_type')
        tier_count   = Get-Prop $Application 'tier_count'
        member_count = Get-Prop $Application 'member_count'
    }
}

$result = [ordered]@{
    scenarios = [ordered]@{}
    moduleImport = $null
}

try {
    Import-Module -Name $ModulePath -Force -ErrorAction Stop
    $result.moduleImport = 'ok'
}
catch {
    $result.moduleImport = "failed: $($_.Exception.Message)"
    $result | ConvertTo-Json -Depth 12 | Set-Content -Path $OutputFile -Encoding utf8
    exit 0
}

$base = @{ Server = "http://127.0.0.1:$Port" }
$sessions = @{}

function Invoke-Scenario {
    param([string] $Name, [scriptblock] $Body)
    $entry = [ordered]@{ status = 'ok'; error = $null }
    try {
        $payload = & $Body
        foreach ($key in $payload.Keys) { $entry[$key] = $payload[$key] }
    }
    catch {
        $entry.status = 'threw'
        $entry.error = $_.Exception.Message
        $entry.errorType = $_.Exception.GetType().FullName

        # The contract requires non-2xx API responses to surface as a
        # VMware.Sdk.Vcf.Ops ApiError model on the ErrorRecord target.
        $target = $_.TargetObject
        if ($null -ne $target) {
            $entry.targetTypeFullName = $target.GetType().FullName
            $entry.targetAssembly = $target.GetType().Assembly.GetName().Name
            $entry.targetCode = Get-Prop $target 'Code'
            $entry.targetMessage = [string](Get-Prop $target 'Message')
            $details = Get-Prop $target 'Details'
            if ($null -ne $details) {
                $entry.targetDetails = @(
                    foreach ($detail in $details) {
                        [ordered]@{
                            code    = Get-Prop $detail 'Code'
                            message = [string](Get-Prop $detail 'Message')
                            target  = @(Get-Prop $detail 'Target')
                        }
                    }
                )
            }
        }
    }
    $script:result.scenarios[$Name] = $entry
}

# --- S1: connect with no domain supplied -----------------------------------
Invoke-Scenario 'connect_plain' {
    $session = Connect-VcfOnServer @base -Credential (New-Cred $USERNAME $PASSWORD)
    $sessions['plain'] = $session
    @{
        token   = [string](Get-Prop $session 'Token')
        baseUri = [string](Get-Prop $session 'BaseUri')
        expiry  = Get-Prop $session 'Expiry'
    }
}

# --- S2: connect with an LDAP domain and a value ----------------------------
Invoke-Scenario 'connect_ldap' {
    $session = Connect-VcfOnServer @base -Credential (New-Cred $USERNAME $PASSWORD) `
        -DomainType 'LDAP' -DomainValue 'corp.example.com'
    @{ token = [string](Get-Prop $session 'Token') }
}

# --- S3: connect with a LOCAL domain and no value ---------------------------
Invoke-Scenario 'connect_local' {
    $session = Connect-VcfOnServer @base -Credential (New-Cred $USERNAME $PASSWORD) `
        -DomainType 'LOCAL'
    @{ token = [string](Get-Prop $session 'Token') }
}

# --- S4: DomainValue alone must not materialise the optional domain object ---
Invoke-Scenario 'connect_value_only' {
    $session = Connect-VcfOnServer @base -Credential (New-Cred $USERNAME $PASSWORD) `
        -DomainValue 'must-not-reach-the-wire.example.com'
    @{ token = [string](Get-Prop $session 'Token') }
}

# --- S5: full paginated sweep with an explicit small page size --------------
Invoke-Scenario 'list_paged' {
    $applications = @(Get-VcfOnApplication -Session $sessions['plain'] -PageSize 3)
    @{ applications = @($applications | ForEach-Object { ConvertTo-Record $_ }) }
}

# --- S6: default page size plus a bound ModifiedAfter -----------------------
Invoke-Scenario 'list_default' {
    $applications = @(Get-VcfOnApplication -Session $sessions['plain'] -ModifiedAfter 1700000000000)
    @{ applications = @($applications | ForEach-Object { ConvertTo-Record $_ }) }
}

# --- S7: a list failure must surface the SDK ApiError model -----------------
Invoke-Scenario 'list_failure' {
    $invalidSession = [pscustomobject]@{
        Connection = Get-Prop $sessions['plain'] 'Connection'
        Token      = 'invalid-token'
    }
    $applications = @(Get-VcfOnApplication -Session $invalidSession)
    @{ unexpectedCount = $applications.Count }
}

# --- S8: a detail failure must surface the SDK ApiError model ---------------
Invoke-Scenario 'detail_failure' {
    $applications = @(Get-VcfOnApplication -Session $sessions['plain'] `
        -ModifiedAfter 1700000000001)
    @{ unexpectedCount = $applications.Count }
}

# --- S9: a repeated cursor must be rejected before re-requesting its page ----
Invoke-Scenario 'repeated_cursor' {
    $applications = @(Get-VcfOnApplication -Session $sessions['plain'] `
        -PageSize 3 -ModifiedAfter 1700000000002)
    @{ unexpectedCount = $applications.Count }
}

# --- S10: rejected credentials must surface the SDK ApiError model ----------
Invoke-Scenario 'auth_failure' {
    $session = Connect-VcfOnServer @base -Credential (New-Cred $USERNAME 'wrong-password')
    @{ unexpectedToken = [string](Get-Prop $session 'Token') }
}

$result | ConvertTo-Json -Depth 12 | Set-Content -Path $OutputFile -Encoding utf8
exit 0
