Set-StrictMode -Version Latest

<#
.SYNOPSIS
    Brings a VCF Operations maintenance schedule to the requested definition.

.DESCRIPTION
    Set-VcfOpsMaintenanceWindow is the declarative entry point used by the
    maintenance orchestrator. The orchestrator re-runs a plan whenever a step is
    interrupted, so this function has to converge on the requested definition
    without ever leaving a second schedule behind for the same key.

    The authoritative wire projection lives in docs/contract.json and its
    upstream provenance in docs/official_sources.json.

.NOTES
    Not implemented yet.
#>
function Set-VcfOpsMaintenanceWindow {
    [CmdletBinding()]
    [OutputType([psobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNull()]
        $Server,

        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string] $Key,

        [Parameter(Mandatory = $true)]
        [ValidateSet('ONCE', 'DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY')]
        [string] $ScheduleType,

        [Parameter(Mandatory = $true)]
        [int] $Hour,

        [Parameter(Mandatory = $true)]
        [int] $MinuteOfTheHour,

        [Parameter(Mandatory = $true)]
        [int] $DurationMinutes,

        [Parameter()]
        [int] $Recurrence,

        [Parameter()]
        [string[]] $DaysOfTheWeek,

        [Parameter()]
        [string] $ExpirationDate,

        [Parameter()]
        [int] $ExpireRuns,

        [Parameter()]
        [string] $TimeZone
    )

    throw [System.NotImplementedException]::new(
        'Set-VcfOpsMaintenanceWindow is not implemented yet.')
}

Export-ModuleMember -Function 'Set-VcfOpsMaintenanceWindow'
