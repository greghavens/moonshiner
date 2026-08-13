<#
    Scenario runner. Executed by tests/Invoke-Verification.ps1 in a dedicated
    child process so no SDK connection state can leak between scenarios.

    Emits the outcome as JSON to -ResultPath. Nothing is written to stdout,
    because the VMware modules emit banners there.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $ManifestPath,
    [Parameter(Mandatory)][int]    $Port,
    [Parameter(Mandatory)][ValidateSet('minimal', 'full', 'precheck-fail')][string] $Scenario,
    [Parameter(Mandatory)][string] $ResultPath
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Get-Member2 {
    param($InputObject, [string] $Name)
    if ($null -eq $InputObject) { return $null }
    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

$outcome = [ordered]@{
    threw             = $false
    errorMessage      = $null
    resultCount       = 0
    resultProperties  = @()
    status            = $null
    precheckPassed    = $null
    adapterInstanceId = $null
    message           = $null
}

try {
    Import-Module -Name $ManifestPath -Force -WarningAction SilentlyContinue

    $credential = [pscredential]::new(
        'svc-vcfops',
        (ConvertTo-SecureString 'Precheck!23' -AsPlainText -Force))

    $common = @{
        Server               = '127.0.0.1'
        Port                 = $Port
        Protocol             = 'http'
        Credential           = $credential
        AuthSource           = 'local'
        AdapterKindKey       = 'VMWARE'
        SkipCertificateCheck = $true
    }

    switch ($Scenario) {
        'full' {
            $identifiers = [ordered]@{
                VCURL               = 'vc01.lab.example.com'
                AUTODISCOVERY       = 'true'
                PROCESSCHANGEEVENTS = 'false'
            }
            $result = @(Register-VcfOpsAdapterInstance @common `
                    -Name 'vc01 Adapter Instance' `
                    -Description 'Primary management vCenter' `
                    -CollectorId '1' `
                    -MonitoringInterval 0 `
                    -ResourceIdentifier $identifiers)
        }
        default {
            # 'minimal' and 'precheck-fail' send the same request; only the
            # mock's precheck verdict differs.
            $result = @(Register-VcfOpsAdapterInstance @common -Name 'vc01 Adapter Instance')
        }
    }

    $outcome.resultCount = $result.Count
    if ($result.Count -ge 1) {
        $first = $result[0]
        $outcome.resultProperties  = @($first.PSObject.Properties.Name)
        $outcome.status            = Get-Member2 -InputObject $first -Name 'Status'
        $outcome.precheckPassed    = Get-Member2 -InputObject $first -Name 'PrecheckPassed'
        $outcome.adapterInstanceId = Get-Member2 -InputObject $first -Name 'AdapterInstanceId'
        $outcome.message           = Get-Member2 -InputObject $first -Name 'Message'
    }
} catch {
    $outcome.threw = $true
    $outcome.errorMessage = [string]$_.Exception.Message
}

$outcome | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ResultPath -Encoding utf8
