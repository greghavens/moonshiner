Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module -Name 'VMware.Sdk.Vcf.Ops' -ErrorAction Stop

<#
    The operations this module is allowed to drive are projected in
    docs/contract.json from the pinned OpenAPI document recorded in
    docs/official_sources.json:

      acquireToken                 POST /suite-api/api/auth/token/acquire
      getCurrentVersionOfServer    GET  /suite-api/api/versions/current
      getMaintenanceSchedules      GET  /suite-api/api/maintenanceschedules
      createMaintenanceSchedules   POST /suite-api/api/maintenanceschedules
      updateMaintenanceSchedules   PUT  /suite-api/api/maintenanceschedules

    The first two are performed by Connect-VcfOpsServer; the caller hands the
    resulting connection to this module. The remaining three have an
    Invoke-VcfOps<OperationId> cmdlet, and request models are built with the
    matching Initialize-VcfOps<schema> cmdlet, for example:

      $schedule = Initialize-VcfOpsschedule -Hour 2 -MinuteOfTheHour 30 `
                                            -Duration 120 -ScheduleType 'DAILY'
      $model    = Initialize-VcfOpsmaintenanceschedule -Key 'k' -Schedule $schedule
      $created  = Invoke-VcfOpsCreateMaintenanceSchedules -Server $Server `
                                            -MaintenanceSchedule $model

    A request model serializes only the properties that were actually supplied,
    so an optional member is kept off the wire by leaving its parameter unbound
    rather than by passing $null, '' or @().

    A non-success HTTP status surfaces as a terminating error whose
    InnerException chain carries a VMware.Binding.OpenApi.Client.ApiException
    with an ErrorCode (the HTTP status) and an ErrorContent (the response body).
#>

function Set-VcfOpsMaintenanceSchedule {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        $Server,

        [Parameter(Mandatory)]
        [string] $Key,

        [Parameter(Mandatory)]
        [int] $Hour,

        [Parameter(Mandatory)]
        [int] $MinuteOfTheHour,

        [Parameter(Mandatory)]
        [int] $DurationMinutes,

        [Parameter(Mandatory)]
        [string] $ScheduleType,

        [Parameter()]
        [int] $Recurrence,

        [Parameter()]
        [string[]] $DaysOfTheWeek,

        [Parameter()]
        [string] $StartDate,

        [Parameter()]
        [string] $ExpirationDate,

        [Parameter()]
        [int] $ExpireRuns,

        [Parameter()]
        [string] $TimeZone
    )

    throw [System.NotImplementedException]::new(
        'Set-VcfOpsMaintenanceSchedule is not implemented yet.')
}

Export-ModuleMember -Function 'Set-VcfOpsMaintenanceSchedule'
