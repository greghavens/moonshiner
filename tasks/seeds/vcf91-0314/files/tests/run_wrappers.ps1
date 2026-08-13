#Requires -Version 7.2
<#
    Protected wrapper probe. Exercises every exported operation wrapper,
    including the optional query and body fields that the triage scenario does
    not itself supply. Results are written as JSON for tests/verify.py.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $BaseUri,
    [Parameter(Mandatory)][string] $AccessToken,
    [Parameter(Mandatory)][string] $DeploymentId,
    [Parameter(Mandatory)][string] $DeploymentName,
    [Parameter(Mandatory)][string] $FailedRequestId,
    [Parameter(Mandatory)][string] $FailureEventId,
    [Parameter(Mandatory)][string] $OutFile
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

try {
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $manifest = Join-Path $repoRoot 'src/VcfAutomation.Triage/VcfAutomation.Triage.psd1'
    Import-Module $manifest -Force -ErrorAction Stop

    $common = @{ BaseUri = $BaseUri; AccessToken = $AccessToken }

    $deployments = @(Get-VcfAutomationDeployment @common `
        -Name $DeploymentName `
        -Status @('CREATE_FAILED', 'UPDATE_FAILED') `
        -Search 'payments-uat' `
        -Page 0 `
        -Size 7)

    $failedRequests = @(Get-VcfAutomationDeploymentRequest @common `
        -DeploymentId $DeploymentId `
        -Status 'FAILED')

    $request = Get-VcfAutomationRequest @common -RequestId $FailedRequestId
    $events = @(Get-VcfAutomationRequestEvent @common -RequestId $FailedRequestId)
    $logs = @(Get-VcfAutomationEventLog @common `
        -RequestId $FailedRequestId `
        -EventId $FailureEventId `
        -SinceRow 4)

    $submitted = Submit-VcfAutomationDeploymentAction @common `
        -DeploymentId $DeploymentId `
        -ActionId 'Deployment.PowerOff' `
        -Reason 'wrapper probe' `
        -Inputs @{ force = $true }

    [pscustomobject]@{
        DeploymentIds       = @($deployments.id)
        FailedRequestIds    = @($failedRequests.id)
        RequestId           = $request.id
        EventCount          = $events.Count
        HasFailureEvent     = $FailureEventId -in @($events.id)
        FirstReturnedLogRow = if ($logs.Count -gt 0) { $logs[0].rownum } else { $null }
        SubmittedRequestId  = $submitted.id
        SubmittedActionId   = $submitted.actionId
    } | ConvertTo-Json -Depth 8 | Set-Content -Path $OutFile -Encoding utf8

    exit 0
}
catch {
    Write-Host '--- wrapper probe failed ---'
    Write-Host $_.Exception.Message
    if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace }
    exit 1
}
