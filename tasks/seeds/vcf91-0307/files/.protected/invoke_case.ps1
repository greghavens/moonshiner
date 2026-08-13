[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ModuleManifest,
    [Parameter(Mandatory)] [uri] $ApiUri,
    [Parameter(Mandatory)] [string] $AccessToken,
    [Parameter(Mandatory)] [string] $CasesFile,
    [Parameter(Mandatory)] [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$WarningPreference = 'Continue'
$InformationPreference = 'Continue'
$VerbosePreference = 'Continue'
$DebugPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$PSStyle.OutputRendering = 'PlainText'
[Globalization.CultureInfo]::CurrentCulture =
    [Globalization.CultureInfo]::GetCultureInfo('tr-TR')

$OUTPUT_PROPERTIES = @(
    'catalogItemId'
    'deploymentId'
    'deploymentName'
    'requestId'
    'requestStatus'
    'requestedBy'
    'totalTasks'
    'completedTasks'
    'pollCount'
    'outputs'
)

Import-Module $ModuleManifest -Force -ErrorAction Stop

$cases = @(
    Get-Content -LiteralPath $CasesFile -Raw -Encoding utf8 |
        ConvertFrom-Json -AsHashtable
)

$secureToken = ConvertTo-SecureString -String $AccessToken -AsPlainText -Force

$command = Get-Command -Name 'New-VcfAutomationDeployment' -CommandType Function
$declaredParameters = @(
    foreach ($parameterAst in $command.ScriptBlock.Ast.Body.ParamBlock.Parameters) {
        $parameterName = $parameterAst.Name.VariablePath.UserPath
        $metadata = $command.Parameters[$parameterName]
        $range = @($metadata.Attributes | Where-Object {
            $_ -is [Management.Automation.ValidateRangeAttribute]
        })
        [ordered] @{
            name      = $parameterName
            type      = $metadata.ParameterType.FullName
            mandatory = [bool] (@($metadata.Attributes | Where-Object {
                $_ -is [Management.Automation.ParameterAttribute] -and $_.Mandatory
            }).Count -gt 0)
            rangeMin  = if ($range.Count -eq 1) { $range[0].MinRange } else { $null }
            rangeMax  = if ($range.Count -eq 1) { $range[0].MaxRange } else { $null }
            default   = if ($null -eq $parameterAst.DefaultValue) {
                $null
            } else {
                $parameterAst.DefaultValue.Extent.Text
            }
        }
    }
)

$firstCase = $cases[0]
$validArguments = @{
    ApiUri              = $ApiUri
    AccessToken         = $secureToken
    CatalogItemId       = [string] $firstCase['catalogItemId']
    ProjectId           = [string] $firstCase['projectId']
    DeploymentName      = [string] $firstCase['deploymentName']
    PollIntervalSeconds = 0
    TimeoutSeconds      = 30
}
$validationDefinitions = @(
    [ordered] @{ name = 'relative-api-uri'; key = 'ApiUri'; value = [uri] 'relative/path' }
    [ordered] @{ name = 'null-access-token'; key = 'AccessToken'; value = $null }
    [ordered] @{ name = 'blank-catalog-item'; key = 'CatalogItemId'; value = '   ' }
    [ordered] @{ name = 'blank-project'; key = 'ProjectId'; value = "`t" }
    [ordered] @{ name = 'blank-deployment-name'; key = 'DeploymentName'; value = '' }
    [ordered] @{ name = 'poll-below-range'; key = 'PollIntervalSeconds'; value = -1 }
    [ordered] @{ name = 'poll-above-range'; key = 'PollIntervalSeconds'; value = 61 }
    [ordered] @{ name = 'timeout-below-range'; key = 'TimeoutSeconds'; value = 0 }
    [ordered] @{ name = 'timeout-above-range'; key = 'TimeoutSeconds'; value = 901 }
)
$validationResults = @(
    foreach ($definition in $validationDefinitions) {
        $validationArguments = $validArguments.Clone()
        $validationArguments[$definition['key']] = $definition['value']
        $emitted = [Collections.Generic.List[object]]::new()
        $rejected = $false
        $errorType = ''
        try {
            New-VcfAutomationDeployment @validationArguments -ErrorAction Stop |
                ForEach-Object { $emitted.Add($_) }
        } catch {
            $rejected = $true
            $errorType = [string] $_.Exception.GetType().FullName
        }
        [ordered] @{
            name        = [string] $definition['name']
            rejected    = $rejected
            objectCount = $emitted.Count
            errorType   = $errorType
        }
    }
)

$results = @(
    foreach ($case in $cases) {
        $arguments = @{
            ApiUri              = $ApiUri
            AccessToken         = $secureToken
            CatalogItemId       = [string] $case['catalogItemId']
            ProjectId           = [string] $case['projectId']
            DeploymentName      = [string] $case['deploymentName']
            PollIntervalSeconds = [int] $case['pollIntervalSeconds']
            TimeoutSeconds      = [int] $case['timeoutSeconds']
        }
        foreach ($optional in @('Version', 'Reason')) {
            $key = $optional.Substring(0, 1).ToLowerInvariant() + $optional.Substring(1)
            if ($case.ContainsKey($key)) {
                $arguments[$optional] = [string] $case[$key]
            }
        }
        if ($case.ContainsKey('inputs')) {
            $arguments['Inputs'] = [hashtable] $case['inputs']
        }

        $record = [ordered] @{
            name           = [string] $case['name']
            outcome        = 'error'
            objectCount    = 0
            propertyOrder  = ''
            values         = $null
            errorType      = ''
            errorMessage   = ''
        }
        try {
            $returned = @(New-VcfAutomationDeployment @arguments -ErrorAction Stop)
            $record['outcome'] = 'success'
            $record['objectCount'] = $returned.Count
            if ($returned.Count -eq 1) {
                $result = $returned[0]
                $record['propertyOrder'] = ($result.PSObject.Properties.Name -join ',')
                $projected = [ordered] @{}
                foreach ($name in $OUTPUT_PROPERTIES) {
                    $property = $result.PSObject.Properties[$name]
                    $projected[$name] = if ($null -eq $property) { $null } else { $property.Value }
                }
                $record['values'] = $projected
            }
        } catch {
            $record['outcome'] = 'error'
            $record['errorType'] = [string] $_.Exception.GetType().FullName
            $record['errorMessage'] = [string] $_.Exception.Message
        }
        $record
    }
)

$output = [ordered] @{
    exportedFunctions = @($command.Module.ExportedFunctions.Keys)
    parameters        = $declaredParameters
    validations       = $validationResults
    caseCount         = $results.Count
    results           = $results
}
$json = $output | ConvertTo-Json -Depth 20 -Compress
[IO.File]::WriteAllText($OutputPath, $json, [Text.UTF8Encoding]::new($false))
