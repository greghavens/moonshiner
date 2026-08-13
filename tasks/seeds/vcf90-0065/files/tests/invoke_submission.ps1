[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $ManifestPath,

    [Parameter(Mandatory)]
    [int] $Port,

    [Parameter(Mandatory)]
    [string] $OutputPath
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

Import-Module $ManifestPath -Force -ErrorAction Stop

function ConvertTo-Projection {
    param([Parameter(Mandatory)] [object] $Result)

    $steps = @(
        foreach ($step in $Result.Steps) {
            [ordered]@{
                index       = [int] $step.Index
                operationId = [string] $step.OperationId
                status      = [string] $step.Status
                statusCode  = $(if ($null -eq $step.StatusCode) { $null } else { [int] $step.StatusCode })
                message     = [string] $step.Message
            }
        }
    )

    return [ordered]@{
        succeeded       = [bool] $Result.Succeeded
        pluginId        = [string] $Result.PluginId
        ruleId          = [string] $Result.RuleId
        requiresCleanup = [bool] $Result.RequiresCleanup
        steps           = $steps
    }
}

$connection = $null
try {
    $connection = Connect-VcfOpsServer `
        -Server '127.0.0.1' `
        -Port $Port `
        -Protocol 'https' `
        -User 'seed-user' `
        -Password (ConvertTo-SecureString 'seed-password' -AsPlainText -Force) `
        -NotDefault `
        -IgnoreInvalidCertificate `
        -ErrorAction Stop

    # Scenario A: nothing optional is supplied. The rule is rejected because the
    # server requires a notification template, so the third step fails after the
    # first two have already changed the appliance.
    $partial = New-VcfOpsOutboundNotification `
        -Server $connection `
        -PluginName 'seed-webhook' `
        -PluginTypeId 'RestPlugin' `
        -RuleName 'seed-critical-rule' `
        -ErrorAction Stop

    # Scenario B: every optional is supplied and the whole change succeeds.
    $complete = New-VcfOpsOutboundNotification `
        -Server $connection `
        -PluginName 'seed-webhook-2' `
        -PluginTypeId 'RestPlugin' `
        -RuleName 'seed-rule-2' `
        -Description 'Critical alert relay' `
        -ConfigValues @{
            URL = 'https://hooks.example.com/vcf'
            METHOD = 'POST'
            ZED = 'uppercase-key'
            alpha = 'lowercase-key'
        } `
        -TemplateId 'tmpl-critical-webhook' `
        -Criticalities @('CRITICAL', 'IMMEDIATE') `
        -ErrorAction Stop

    $projection = [ordered]@{
        partial  = ConvertTo-Projection -Result $partial
        complete = ConvertTo-Projection -Result $complete
    }

    [System.IO.File]::WriteAllText(
        $OutputPath,
        ($projection | ConvertTo-Json -Depth 8 -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )
}
finally {
    if ($null -ne $connection) {
        Disconnect-VcfOpsServer -Server $connection -ErrorAction SilentlyContinue | Out-Null
    }
}
