#Requires -Version 7.4
<#
    Protected harness. Establishes a genuine VMware.Sdk.Vcf.Ops session against
    the loopback mock, then drives Set-VcfOpsMaintenanceWindow through one
    scenario and writes the returned results as JSON for the verifier.

    The mock starts with no maintenance schedules. Every scenario reaches the
    state it needs only through the calls the module under test makes.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet(
        'create-then-retry',
        'drift-update',
        'weekly-omission',
        'full-convergence'
    )]
    [string] $Scenario,

    [Parameter(Mandatory = $true)][int] $Port,

    [Parameter(Mandatory = $true)][string] $ResultPath,

    [Parameter(Mandatory = $true)][string] $ModulePath
)

$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'

$results = [System.Collections.Generic.List[object]]::new()

function Add-Result {
    param([string] $Label, $Returned)

    $record = [ordered]@{ call = $Label }
    foreach ($name in 'Action', 'Id', 'Key') {
        $property = $null
        if ($null -ne $Returned -and $Returned.PSObject.Properties[$name]) {
            $property = $Returned.PSObject.Properties[$name].Value
        }
        $record[$name.ToLowerInvariant()] = if ($null -eq $property) { $null } else { [string] $property }
    }
    $record['type'] = if ($null -eq $Returned) { '<null>' } else { $Returned.GetType().FullName }
    $results.Add([pscustomobject] $record)
}

function Write-Results {
    param([string] $ErrorText)

    $payload = [ordered]@{
        scenario = $Scenario
        calls    = $results.ToArray()
        error    = $ErrorText
    }
    $json = $payload | ConvertTo-Json -Depth 8
    Set-Content -LiteralPath $ResultPath -Value $json -Encoding utf8
}

try {
    Import-Module VMware.Sdk.Vcf.Ops -ErrorAction Stop
    Import-Module $ModulePath -Force -ErrorAction Stop

    $password = ConvertTo-SecureString 'mock-password' -AsPlainText -Force
    $server = Connect-VcfOpsServer -Server '127.0.0.1' -Protocol 'http' -Port $Port `
        -User 'maintenance-operator' -Password $password -IgnoreInvalidCertificate -NotDefault

    $key = "fleet-patch-window-$Scenario"

    switch ($Scenario) {
        'create-then-retry' {
            $common = @{
                Server          = $server
                Key             = $key
                ScheduleType    = 'DAILY'
                Hour            = 2
                MinuteOfTheHour = 30
                DurationMinutes = 120
                Recurrence      = 1
            }
            Add-Result 'first' (Set-VcfOpsMaintenanceWindow @common)
            Add-Result 'retry' (Set-VcfOpsMaintenanceWindow @common)
        }

        'drift-update' {
            $common = @{
                Server          = $server
                Key             = $key
                ScheduleType    = 'DAILY'
                Hour            = 2
                MinuteOfTheHour = 30
                DurationMinutes = 120
                Recurrence      = 1
            }
            Add-Result 'create' (Set-VcfOpsMaintenanceWindow @common)

            $drifted = @{} + $common
            $drifted['DurationMinutes'] = 240
            Add-Result 'drift' (Set-VcfOpsMaintenanceWindow @drifted)
            Add-Result 'settle' (Set-VcfOpsMaintenanceWindow @drifted)
        }

        'weekly-omission' {
            $weekly = @{
                Server          = $server
                Key             = $key
                ScheduleType    = 'WEEKLY'
                Hour            = 23
                MinuteOfTheHour = 15
                DurationMinutes = 90
                DaysOfTheWeek   = @('SATURDAY', 'SUNDAY')
                ExpirationDate  = '11/30/2027'
            }
            Add-Result 'create' (Set-VcfOpsMaintenanceWindow @weekly)

            # Same definition, days supplied in the opposite order. A schedule
            # that already matches must not be rewritten.
            $reordered = @{} + $weekly
            $reordered['DaysOfTheWeek'] = @('SUNDAY', 'SATURDAY')
            Add-Result 'reordered' (Set-VcfOpsMaintenanceWindow @reordered)

            # Duplicate entries do not change a set either.
            $duplicated = @{} + $weekly
            $duplicated['DaysOfTheWeek'] = @('SATURDAY', 'SUNDAY', 'SATURDAY')
            Add-Result 'duplicated' (Set-VcfOpsMaintenanceWindow @duplicated)
        }

        'full-convergence' {
            # Start with every supported optional field bound, then alter one
            # field per call. This proves that every field participates in
            # drift detection instead of letting a change to some other field
            # hide an ignored comparison.
            $desired = @{
                Server          = $server
                Key             = $key
                ScheduleType    = 'DAILY'
                Hour            = 1
                MinuteOfTheHour = 5
                DurationMinutes = 30
                Recurrence      = 2
                DaysOfTheWeek   = @('MONDAY', 'WEDNESDAY')
                ExpirationDate  = '12/31/2028'
                ExpireRuns      = 7
                TimeZone        = 'UTC'
            }
            Add-Result 'create' (Set-VcfOpsMaintenanceWindow @desired)

            $changes = [ordered]@{
                'schedule-type' = @{ ScheduleType = 'WEEKLY' }
                'hour'          = @{ Hour = 4 }
                'minute'        = @{ MinuteOfTheHour = 45 }
                'duration'      = @{ DurationMinutes = 75 }
                'recurrence'    = @{ Recurrence = 3 }
                'days'          = @{ DaysOfTheWeek = @('THURSDAY', 'FRIDAY') }
                'expiration'    = @{ ExpirationDate = '01/31/2029' }
                'expire-runs'   = @{ ExpireRuns = 11 }
                'time-zone'     = @{ TimeZone = 'America/Chicago' }
            }
            foreach ($change in $changes.GetEnumerator()) {
                foreach ($entry in $change.Value.GetEnumerator()) {
                    $desired[$entry.Key] = $entry.Value
                }
                Add-Result $change.Key (Set-VcfOpsMaintenanceWindow @desired)
            }

            # Omit one stored optional field at a time. Each omission is drift,
            # and each update must project every still-bound field while
            # clearing the newly unbound one.
            $removals = [ordered]@{
                'remove-recurrence' = 'Recurrence'
                'remove-days'       = 'DaysOfTheWeek'
                'remove-expiration' = 'ExpirationDate'
                'remove-expire-runs'= 'ExpireRuns'
                'remove-time-zone'  = 'TimeZone'
            }
            foreach ($removal in $removals.GetEnumerator()) {
                [void] $desired.Remove($removal.Value)
                Add-Result $removal.Key (Set-VcfOpsMaintenanceWindow @desired)
            }
            Add-Result 'settle' (Set-VcfOpsMaintenanceWindow @desired)
        }
    }

    Write-Results -ErrorText $null
    exit 0
}
catch {
    Write-Results -ErrorText ($_ | Out-String)
    Write-Error ($_ | Out-String) -ErrorAction Continue
    exit 1
}
# The session is deliberately left open: releaseToken is not one of the
# operations docs/contract.json names, so the mock must never see it.
