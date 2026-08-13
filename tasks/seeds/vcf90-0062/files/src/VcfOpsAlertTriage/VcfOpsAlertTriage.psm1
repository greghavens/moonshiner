Set-StrictMode -Version Latest

<#
.SYNOPSIS
    Runs one alert triage sweep against a VMware Cloud Foundation Operations 9.0
    deployment.

.DESCRIPTION
    Not implemented yet. See README.md for the run configuration schema, the
    triage policy and the summary object this function has to return.

.PARAMETER ConfigPath
    Path to the JSON run configuration.
#>
function Invoke-VcfOpsAlertTriage {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ConfigPath
    )

    throw [System.NotImplementedException]::new('Invoke-VcfOpsAlertTriage has not been implemented yet.')
}

Export-ModuleMember -Function 'Invoke-VcfOpsAlertTriage'
