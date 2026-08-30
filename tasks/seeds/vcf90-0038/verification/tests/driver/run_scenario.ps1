# PROTECTED FILE -- do not modify.
#
# Drives the candidate VcfEvcGuard module against the loopback fixture and
# writes the returned object to -OutFile as JSON so the Python test can assert
# on it. Any terminating error is captured and reported in the same file.

[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ModulePath,
    [Parameter(Mandatory)] [int]    $Port,
    [Parameter(Mandatory)] [string] $Cluster,
    [Parameter(Mandatory)] [string] $OutFile,
    # JSON object for -EvcMode, or the literal string 'none' to omit the
    # parameter entirely (which asks vCenter to clear the cluster's EVC mode).
    [string] $EvcModeJson = 'none',
    [double] $PollIntervalSeconds = 0.02,
    [double] $TimeoutSeconds = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function ConvertTo-Hashtable {
    param([Parameter(ValueFromPipeline)] $InputObject)
    process {
        if ($null -eq $InputObject) { return $null }
        if ($InputObject -is [System.Collections.IEnumerable] -and $InputObject -isnot [string]) {
            $items = @(foreach ($item in $InputObject) { ConvertTo-Hashtable $item })
            # A leading comma writes the array as one pipeline object. Without
            # it PowerShell collapses one-element arrays to their sole item and
            # empty arrays to $null while returning from this helper.
            return ,$items
        }
        if ($InputObject -is [psobject] -and $InputObject.PSObject.Properties.Name.Count -gt 0 -and
            $InputObject -isnot [string] -and $InputObject -isnot [valuetype]) {
            $table = @{}
            foreach ($property in $InputObject.PSObject.Properties) {
                $table[$property.Name] = ConvertTo-Hashtable $property.Value
            }
            return $table
        }
        return $InputObject
    }
}

$report = [ordered]@{
    ok      = $false
    error   = $null
    result  = $null
    loaded  = @()
}

try {
    Import-Module $ModulePath -Force -ErrorAction Stop -WarningAction SilentlyContinue

    $report.loaded = @(
        Get-Module | Where-Object { $_.Name -like 'VMware.Sdk.Vcf.*' } |
            ForEach-Object { $_.Name }
    )

    $securePassword = ConvertTo-SecureString 'VMw@re1!VMw@re1!' -AsPlainText -Force
    $credential = [System.Management.Automation.PSCredential]::new(
        'administrator@vsphere.local', $securePassword)

    $connection = Connect-VcfEvcGuardServer `
        -Server '127.0.0.1' `
        -Port $Port `
        -Protocol 'http' `
        -Credential $credential `
        -IgnoreInvalidCertificate

    $arguments = @{
        Connection          = $connection
        Cluster             = $Cluster
        PollIntervalSeconds = $PollIntervalSeconds
        TimeoutSeconds      = $TimeoutSeconds
    }

    if ($EvcModeJson -ne 'none') {
        $arguments['EvcMode'] = ConvertTo-Hashtable (
            $EvcModeJson | ConvertFrom-Json -ErrorAction Stop)
    }

    $result = Invoke-VcfEvcModeGuardedSet @arguments

    $report.result = $result
    $report.ok = $true
}
catch {
    $report.error = ($_ | Out-String)
}

$report | ConvertTo-Json -Depth 24 | Set-Content -LiteralPath $OutFile -Encoding utf8
