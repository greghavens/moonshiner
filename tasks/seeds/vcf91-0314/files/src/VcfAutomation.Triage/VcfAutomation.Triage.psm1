#Requires -Version 7.2

Set-StrictMode -Version Latest

<#
    VcfAutomation.Triage

    Thin wrappers over the VCF Automation 9.1 "VM Apps Org - Deployment"
    operations, plus one orchestrator that walks a failed deployment down to the
    log line that explains it.

    The operations, their parameters and their response shapes are defined in
    docs/contract.json. That contract was transcribed from Broadcom's xAPIs
    reference pages (see docs/official_sources.json) because VCF Automation
    publishes no API specification.

    NOTHING IN HERE IS IMPLEMENTED YET.
#>

function Get-VcfAutomationDeployment {
    <#
    .SYNOPSIS
        Get Deployments (GET /deployment/api/deployments).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string] $BaseUri,
        [Parameter(Mandatory)][string] $AccessToken,
        [string] $Name,
        [string[]] $Status,
        [string] $Search,
        [int] $Page,
        [int] $Size
    )

    throw [System.NotImplementedException]::new('Get-VcfAutomationDeployment is not implemented.')
}

function Get-VcfAutomationDeploymentRequest {
    <#
    .SYNOPSIS
        Get Deployment Requests (GET /deployment/api/deployments/{deploymentId}/requests).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string] $BaseUri,
        [Parameter(Mandatory)][string] $AccessToken,
        [Parameter(Mandatory)][string] $DeploymentId,
        [string] $Status
    )

    throw [System.NotImplementedException]::new('Get-VcfAutomationDeploymentRequest is not implemented.')
}

function Get-VcfAutomationRequest {
    <#
    .SYNOPSIS
        Get Request (GET /deployment/api/requests/{requestId}).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string] $BaseUri,
        [Parameter(Mandatory)][string] $AccessToken,
        [Parameter(Mandatory)][string] $RequestId
    )

    throw [System.NotImplementedException]::new('Get-VcfAutomationRequest is not implemented.')
}

function Get-VcfAutomationRequestEvent {
    <#
    .SYNOPSIS
        Get Request Events (GET /deployment/api/requests/{requestId}/events).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string] $BaseUri,
        [Parameter(Mandatory)][string] $AccessToken,
        [Parameter(Mandatory)][string] $RequestId
    )

    throw [System.NotImplementedException]::new('Get-VcfAutomationRequestEvent is not implemented.')
}

function Get-VcfAutomationEventLog {
    <#
    .SYNOPSIS
        Get Event Logs (GET /deployment/api/requests/{requestId}/events/{eventId}/logs).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string] $BaseUri,
        [Parameter(Mandatory)][string] $AccessToken,
        [Parameter(Mandatory)][string] $RequestId,
        [Parameter(Mandatory)][string] $EventId,
        [int] $SinceRow
    )

    throw [System.NotImplementedException]::new('Get-VcfAutomationEventLog is not implemented.')
}

function Submit-VcfAutomationDeploymentAction {
    <#
    .SYNOPSIS
        Submit Deployment Action Request
        (POST /deployment/api/deployments/{deploymentId}/requests).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string] $BaseUri,
        [Parameter(Mandatory)][string] $AccessToken,
        [Parameter(Mandatory)][string] $DeploymentId,
        [string] $ActionId,
        [string] $Reason,
        [hashtable] $Inputs
    )

    throw [System.NotImplementedException]::new('Submit-VcfAutomationDeploymentAction is not implemented.')
}

function Invoke-VcfAutomationDeploymentTriage {
    <#
    .SYNOPSIS
        Diagnose a failed VCF Automation deployment and submit the remediation.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string] $BaseUri,
        [Parameter(Mandatory)][string] $AccessToken,
        [Parameter(Mandatory)][string] $DeploymentName
    )

    throw [System.NotImplementedException]::new('Invoke-VcfAutomationDeploymentTriage is not implemented.')
}

Export-ModuleMember -Function @(
    'Get-VcfAutomationDeployment'
    'Get-VcfAutomationDeploymentRequest'
    'Get-VcfAutomationRequest'
    'Get-VcfAutomationRequestEvent'
    'Get-VcfAutomationEventLog'
    'Submit-VcfAutomationDeploymentAction'
    'Invoke-VcfAutomationDeploymentTriage'
)
