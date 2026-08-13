#Requires -Version 7.4
<#
.SYNOPSIS
    Drives Save-VcfOnDiscoveredApplication against the loopback mock.

.DESCRIPTION
    Reads a job description produced by tests/verify.py, runs every job in a
    single PowerShell process, and writes one JSON result document. Nothing in
    this script asserts anything; verify.py owns every assertion.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $JobFile
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$plan = Get-Content -LiteralPath $JobFile -Raw | ConvertFrom-Json -AsHashtable

Import-Module -Name $plan.Module -Force -ErrorAction Stop -WarningAction SilentlyContinue

$results = @()

foreach ($job in $plan.Jobs) {
    $securePassword = ConvertTo-SecureString -String $job.Password -AsPlainText -Force
    $credential = [pscredential]::new($job.Username, $securePassword)

    $splat = @{
        Server              = $job.Server
        Credential          = $credential
        DiscoveryType       = $job.DiscoveryType
        PollIntervalSeconds = [double]$job.PollIntervalSeconds
        TimeoutSeconds      = [double]$job.TimeoutSeconds
    }

    if ($job.ContainsKey('DomainType')) { $splat['DomainType'] = [string]$job.DomainType }
    if ($job.ContainsKey('DomainValue')) { $splat['DomainValue'] = [string]$job.DomainValue }
    if ($job.ContainsKey('Granularity')) { $splat['Granularity'] = [string]$job.Granularity }
    if ($job.ContainsKey('PageSize')) { $splat['PageSize'] = [int]$job.PageSize }
    if ($job.ContainsKey('EnableIntent')) { $splat['EnableIntent'] = [bool]$job.EnableIntent }

    $entry = [ordered]@{
        name       = $job.Name
        ok         = $false
        error      = $null
        error_type = $null
        result     = $null
    }

    try {
        $output = Save-VcfOnDiscoveredApplication @splat
        $entry['ok'] = $true

        $shaped = [ordered]@{}
        foreach ($property in 'RequestId', 'Status', 'Progress', 'TaskName', 'PollCount') {
            $shaped[$property] = if ($output.PSObject.Properties.Name -contains $property) {
                $output.$property
            }
            else { $null }
        }
        foreach ($property in 'DiscoveredEntityIds', 'SavedApplications') {
            if ($output.PSObject.Properties.Name -contains $property) {
                $shaped[$property] = @($output.$property)
            }
            else {
                $shaped[$property] = @()
            }
        }
        $entry['result'] = $shaped
    }
    catch {
        $entry['ok'] = $false
        $entry['error'] = [string]$_.Exception.Message
        $entry['error_type'] = $_.Exception.GetType().FullName
    }

    $results += [pscustomobject]$entry
}

$document = [pscustomobject]@{
    powershell = $PSVersionTable.PSVersion.ToString()
    jobs       = @($results)
}

$document | ConvertTo-Json -Depth 12 |
    Set-Content -LiteralPath $plan.Output -Encoding utf8NoBOM

Write-Host ("exercise.ps1 completed {0} job(s)" -f $results.Count)
