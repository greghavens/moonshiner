[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ModuleManifest,
    [Parameter(Mandatory)] [string] $ServiceUri,
    [Parameter(Mandatory)] [string] $AccessToken,
    [Parameter(Mandatory)] [string] $DepotFqdn,
    [Parameter(Mandatory)] [string] $DepotCertificate,
    [Parameter(Mandatory)] [string] $PlanPath,
    [Parameter(Mandatory)] [string] $OptionsPath,
    [Parameter(Mandatory)] [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$PSStyle.OutputRendering = 'PlainText'

Import-Module $ModuleManifest -Force -ErrorAction Stop

$plan = Get-Content -LiteralPath $PlanPath -Raw | ConvertFrom-Json
# Only the optional inputs this case actually exercises are present as keys.
$options = Get-Content -LiteralPath $OptionsPath -Raw | ConvertFrom-Json
$suppliedOptions = @(foreach ($property in $options.PSObject.Properties) { $property.Name })

$serviceUriValue = [uri] $ServiceUri
$secureAccessToken = ConvertTo-SecureString $AccessToken -AsPlainText -Force

try {
    $arguments = @{
        ServiceUri          = $serviceUriValue
        AccessToken         = $secureAccessToken
        Component           = $plan
        DepotFqdn           = $DepotFqdn
        DepotCertificate    = $DepotCertificate
        PollIntervalSeconds = 0
        TimeoutSeconds      = 10
    }
    if ($suppliedOptions -contains 'bundleVersion') {
        $arguments['BundleVersion'] = [string] $options.bundleVersion
    }
    if ($suppliedOptions -contains 'depotManifestCertificate') {
        $arguments['DepotManifestCertificate'] = [string[]] @($options.depotManifestCertificate)
    }
    if ($suppliedOptions -contains 'performBackup') {
        $arguments['PerformBackup'] = [bool] $options.performBackup
    }
    if ($suppliedOptions -contains 'correlationId') {
        $arguments['CorrelationId'] = [string] $options.correlationId
    }

    $results = @(Invoke-VcfSddcLcmComponentUpgrade @arguments -ErrorAction Stop)

    if ($results.Count -ne 1) {
        throw "Expected exactly one report object; the function emitted $($results.Count)."
    }
    $report = $results[0]

    $steps = @(
        foreach ($step in @($report.steps)) {
            [ordered] @{
                propertyOrder = ($step.PSObject.Properties.Name -join ',')
                stepNumber    = [int]    $step.stepNumber
                operationId   = [string] $step.operationId
                action        = [string] $step.action
                component     = [string] $step.component
                taskId        = [string] $step.taskId
                status        = [string] $step.status
            }
        }
    )
    $resolved = @(
        foreach ($entry in @($report.resolvedComponents)) {
            [ordered] @{
                component = [string] $entry.component
                version   = [string] $entry.version
                binaryUrl = [string] $entry.binaryUrl
            }
        }
    )

    # Exercise every input rule that must reject before issuing a request. Any
    # implementation that validates after setDepot will leave extra traffic in
    # the mock log in addition to reporting a failed validation check here.
    function Test-RejectedInvocation {
        param([Parameter(Mandatory)] [hashtable] $Arguments)
        try {
            $null = Invoke-VcfSddcLcmComponentUpgrade @Arguments -ErrorAction Stop
            return $false
        } catch {
            return $true
        }
    }

    $validArguments = @{
        ServiceUri          = $serviceUriValue
        AccessToken         = $secureAccessToken
        Component           = $plan
        DepotFqdn           = $DepotFqdn
        DepotCertificate    = $DepotCertificate
        PollIntervalSeconds = 0
        TimeoutSeconds      = 10
    }
    $validationResults = [ordered] @{}

    $candidate = $validArguments.Clone()
    $candidate['DepotFqdn'] = '   '
    $validationResults['blankDepotFqdn'] = Test-RejectedInvocation $candidate

    $candidate = $validArguments.Clone()
    $candidate['DepotCertificate'] = "`t"
    $validationResults['blankDepotCertificate'] = Test-RejectedInvocation $candidate

    $candidate = $validArguments.Clone()
    $candidate['Component'] = [object[]] @()
    $validationResults['emptyComponentList'] = Test-RejectedInvocation $candidate

    foreach ($member in @('Name', 'Id', 'TargetVersion')) {
        $invalidPlan = @(
            for ($index = 0; $index -lt $plan.Count; $index++) {
                $copy = [ordered] @{
                    Name          = [string] $plan[$index].Name
                    Id            = [string] $plan[$index].Id
                    TargetVersion = [string] $plan[$index].TargetVersion
                }
                if ($index -eq 0) { $copy[$member] = '  ' }
                [pscustomobject] $copy
            }
        )
        $candidate = $validArguments.Clone()
        $candidate['Component'] = [object[]] $invalidPlan
        $validationResults["blankComponent$member"] = Test-RejectedInvocation $candidate
    }

    $duplicateName = @(
        for ($index = 0; $index -lt $plan.Count; $index++) {
            [pscustomobject] [ordered] @{
                Name          = if ($index -eq 1) { [string] $plan[0].Name } else { [string] $plan[$index].Name }
                Id            = [string] $plan[$index].Id
                TargetVersion = [string] $plan[$index].TargetVersion
            }
        }
    )
    $candidate = $validArguments.Clone()
    $candidate['Component'] = [object[]] $duplicateName
    $validationResults['duplicateComponentName'] = Test-RejectedInvocation $candidate

    $duplicateId = @(
        for ($index = 0; $index -lt $plan.Count; $index++) {
            [pscustomobject] [ordered] @{
                Name          = [string] $plan[$index].Name
                Id            = if ($index -eq 1) { [string] $plan[0].Id } else { [string] $plan[$index].Id }
                TargetVersion = [string] $plan[$index].TargetVersion
            }
        }
    )
    $candidate = $validArguments.Clone()
    $candidate['Component'] = [object[]] $duplicateId
    $validationResults['duplicateComponentId'] = Test-RejectedInvocation $candidate

    foreach ($rangeCase in @(
        @('pollBelowRange', 'PollIntervalSeconds', -1),
        @('pollAboveRange', 'PollIntervalSeconds', 61),
        @('timeoutBelowRange', 'TimeoutSeconds', 0),
        @('timeoutAboveRange', 'TimeoutSeconds', 901)
    )) {
        $candidate = $validArguments.Clone()
        $candidate[$rangeCase[1]] = $rangeCase[2]
        $validationResults[$rangeCase[0]] = Test-RejectedInvocation $candidate
    }

    # Defaults are part of the requested public signature. Reading the parsed
    # constant values avoids a slow and timing-sensitive test of the 2-second
    # polling default.
    $functionAst = (Get-Command 'Invoke-VcfSddcLcmComponentUpgrade' `
        -CommandType Function -ErrorAction Stop).ScriptBlock.Ast
    $parameterAsts = @($functionAst.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.ParameterAst]
    }, $true))
    function Get-ParameterDefault {
        param([Parameter(Mandatory)] [string] $Name)
        $parameterAst = $parameterAsts | Where-Object {
            $_.Name.VariablePath.UserPath -ceq $Name
        } | Select-Object -First 1
        if ($null -eq $parameterAst -or $null -eq $parameterAst.DefaultValue) {
            return $null
        }
        try { return $parameterAst.DefaultValue.SafeGetValue() } catch { return $null }
    }
    $pollIntervalDefault = Get-ParameterDefault 'PollIntervalSeconds'
    $timeoutDefault = Get-ParameterDefault 'TimeoutSeconds'
    $exportedFunctions = @(
        (Get-Module 'VcfSddcLcmUpgrade' -ErrorAction Stop).ExportedFunctions.Keys |
            Sort-Object
    )

    $output = [ordered] @{
        propertyOrder      = ($report.PSObject.Properties.Name -join ',')
        overallStatus      = [string] $report.overallStatus
        depotFqdn          = [string] $report.depotFqdn
        depotTaskId        = [string] $report.depotTaskId
        resolvedComponents = $resolved
        steps              = $steps
        failedStep         = [int]    $report.failedStep
        failedOperationId  = [string] $report.failedOperationId
        failedAction       = [string] $report.failedAction
        failedComponent    = [string] $report.failedComponent
        failedTaskId       = [string] $report.failedTaskId
        failedStage        = [string] $report.failedStage
        errorMessage       = [string] $report.errorMessage
        notAttempted       = @([string[]] $report.notAttempted)
        validationResults  = $validationResults
        pollIntervalDefault = $pollIntervalDefault
        timeoutDefault     = $timeoutDefault
        exportedFunctions  = $exportedFunctions
    }
    $json = $output | ConvertTo-Json -Depth 12 -Compress
    [IO.File]::WriteAllText($OutputPath, $json, [Text.UTF8Encoding]::new($false))
} finally { }
