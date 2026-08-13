$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$TaskRoot = Split-Path -Parent $PSScriptRoot
$ModulePath = Join-Path $TaskRoot 'VcfAutomationDay2.psm1'
$ContractPath = Join-Path $TaskRoot 'docs/contract.json'
$SourcesPath = Join-Path $TaskRoot 'docs/official_sources.json'
$MockPath = Join-Path $PSScriptRoot 'mock_vcf_automation.py'

$AccessTokenText = 'loopback-automation-access-token'
$DeploymentId = '7f1c2b40-6a1e-4c8d-9f22-1b0e8d3a5c47'
$ReferenceRoot = 'https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest/'
$RetrievedOn = '2026-08-11'

function Assert-True {
    param(
        [Parameter(Mandatory)][bool]$Condition,
        [Parameter(Mandatory)][string]$Message
    )
    if (-not $Condition) {
        throw "Verification failed: $Message"
    }
}

function Assert-Equal {
    param(
        [AllowNull()][object]$Actual,
        [AllowNull()][object]$Expected,
        [Parameter(Mandatory)][string]$Message
    )
    if ($null -eq $Actual -and $null -eq $Expected) { return }
    if ($null -eq $Actual -or $null -eq $Expected -or "$Actual" -cne "$Expected") {
        throw "Verification failed: $Message. Expected '$Expected', got '$Actual'."
    }
}

function Test-JsonMember {
    param([AllowNull()][object]$Object, [Parameter(Mandatory)][string]$Name)
    if ($null -eq $Object) { return $false }
    return [bool](@($Object.PSObject.Properties.Name) -ccontains $Name)
}

function Get-JsonMember {
    param([AllowNull()][object]$Object, [Parameter(Mandatory)][string]$Name)
    if (Test-JsonMember $Object $Name) { return $Object.$Name }
    return $null
}

function Assert-MemberOrder {
    param(
        [AllowNull()][object]$Object,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$Names,
        [Parameter(Mandatory)][string]$Message
    )
    Assert-True ($null -ne $Object) "$Message (object was null)"
    $actual = @($Object.PSObject.Properties.Name)
    Assert-Equal ($actual -join ',') ($Names -join ',') $Message
}

function Assert-MemberSet {
    param(
        [AllowNull()][object]$Object,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$Names,
        [Parameter(Mandatory)][string]$Message
    )
    Assert-True ($null -ne $Object) "$Message (object was null)"
    $actual = @($Object.PSObject.Properties.Name | Sort-Object)
    $expected = @($Names | Sort-Object)
    Assert-Equal ($actual -join ',') ($expected -join ',') $Message
}

# ---------------------------------------------------------------- layout ----

foreach ($required in @($ModulePath, $ContractPath, $SourcesPath, $MockPath)) {
    Assert-True (Test-Path -LiteralPath $required -PathType Leaf) "Missing required file $required"
}

$vendored = @(Get-ChildItem -LiteralPath $TaskRoot -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
        $_.Extension -in @('.dll', '.nupkg') -or $_.Name -match '^VMware\..*\.ps[dm]1$'
    })
Assert-Equal $vendored.Count 0 'VMware SDK binaries or modules must not be vendored into the task'

$source = Get-Content -LiteralPath $ModulePath -Raw
$tokens = $null
$parseErrors = $null
$sourceAst = [System.Management.Automation.Language.Parser]::ParseInput(
    $source, [ref]$tokens, [ref]$parseErrors
)
Assert-Equal @($parseErrors).Count 0 'The module must parse as valid PowerShell'
$forbiddenCommands = @(
    $sourceAst.FindAll({
            param($node)
            if ($node -isnot [System.Management.Automation.Language.CommandAst]) { return $false }
            $name = $node.GetCommandName()
            return $null -ne $name -and $name.ToLowerInvariant() -in @('curl', 'curl.exe', 'wget', 'wget.exe')
        }, $true)
)
Assert-Equal $forbiddenCommands.Count 0 'The implementation must not shell out to curl or wget'
$forbiddenModuleUses = @(
    $sourceAst.FindAll({
            param($node)
            if ($node -is [System.Management.Automation.Language.UsingStatementAst]) {
                return $node.Extent.Text -cmatch '(?i)\bVMware\.Sdk\.Vcf\.Automation\b'
            }
            if ($node -is [System.Management.Automation.Language.CommandAst] -and
                $node.GetCommandName() -ieq 'Import-Module') {
                return $node.Extent.Text -cmatch '(?i)\bVMware\.Sdk\.Vcf\.Automation\b'
            }
            return $false
        }, $true)
)
Assert-Equal $forbiddenModuleUses.Count 0 'No VMware.Sdk.Vcf.Automation module exists; the implementation must not depend on one'

# -------------------------------------------------------------- contract ----

$contract = Get-Content -LiteralPath $ContractPath -Raw | ConvertFrom-Json -Depth 100
$sources = Get-Content -LiteralPath $SourcesPath -Raw | ConvertFrom-Json -Depth 100

$expectedOperations = @(
    [pscustomobject]@{
        id     = 'getDeploymentResources'
        name   = 'Get Deployment Resources'
        method = 'GET'
        path   = '/deployment/api/deployments/{deploymentId}/resources'
        url    = 'https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest/deployment/api/deployments/deploymentId/resources/get/'
    },
    [pscustomobject]@{
        id     = 'getResourceActions'
        name   = 'Get Resource Actions'
        method = 'GET'
        path   = '/deployment/api/resources/{resourceId}/actions'
        url    = 'https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest/deployment/api/resources/resourceId/actions/get/'
    },
    [pscustomobject]@{
        id     = 'submitResourceActionRequest'
        name   = 'Submit Resource Action Request'
        method = 'POST'
        path   = '/deployment/api/resources/{resourceId}/requests'
        url    = 'https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest/deployment/api/resources/resourceId/requests/post/'
    }
)

Assert-Equal $contract.derived_from.source_kind 'reference documentation' 'The contract must declare that its source is reference documentation'
Assert-Equal $contract.derived_from.specification_available $false 'The contract must state that no published specification exists'
Assert-Equal $contract.derived_from.reference_root $ReferenceRoot 'Contract reference root changed'
Assert-Equal $contract.derived_from.retrieved $RetrievedOn 'Contract retrieval date changed'
Assert-True ($contract.derived_from.note -cmatch 'reference documentation, not a published specification') 'The contract note must state plainly that the source is reference documentation rather than a published specification'
Assert-Equal $contract.api.reference_version '9.1' 'Contract reference version changed'
Assert-Equal $contract.api.authentication.scheme_name 'bearerAuth' 'Contract authentication scheme changed'
Assert-Equal $contract.api.authentication.value_prefix 'Bearer ' 'Contract authentication prefix changed'

Assert-Equal $sources.source_kind 'reference documentation' 'Official sources must declare reference documentation as the source kind'
Assert-Equal $sources.specification_available $false 'Official sources must state that no published specification exists'
Assert-True (@($sources.pages).Count -ge $expectedOperations.Count) 'Official sources must record every fetched reference page'
foreach ($page in @($sources.pages)) {
    Assert-True ($page.url -clike 'https://developer.broadcom.com/*') "Reference page $($page.url) must live on developer.broadcom.com"
    Assert-True (-not [string]::IsNullOrWhiteSpace($page.documents)) "Reference page $($page.url) must record what it documents"
    Assert-Equal $page.retrieved $RetrievedOn "Reference page $($page.url) must record the fetch date"
}

Assert-Equal @($contract.operations).Count $expectedOperations.Count 'The contract must name exactly the three selected operations'
Assert-Equal @($sources.operations).Count $expectedOperations.Count 'Official sources must record exactly the three selected operations'

for ($index = 0; $index -lt $expectedOperations.Count; $index++) {
    $expected = $expectedOperations[$index]
    $operation = @($contract.operations)[$index]
    $record = @($sources.operations)[$index]
    Assert-Equal $operation.contract_operation_id $expected.id "Contract operation id at index $index"
    Assert-Equal $operation.documented_operation_name $expected.name "Contract documented operation name for $($expected.id)"
    Assert-Equal $operation.method $expected.method "Contract method for $($expected.id)"
    Assert-Equal $operation.path $expected.path "Contract path for $($expected.id)"
    Assert-Equal $record.contract_operation_id $expected.id "Official source operation id at index $index"
    Assert-Equal $record.documented_operation_name $expected.name "Official source documented operation name for $($expected.id)"
    Assert-Equal $record.method $expected.method "Official source method for $($expected.id)"
    Assert-Equal $record.path $expected.path "Official source path for $($expected.id)"
    Assert-Equal $record.documentation_url $expected.url "Official source documentation URL for $($expected.id)"
    Assert-Equal $record.retrieved $RetrievedOn "Official source fetch date for $($expected.id)"
}

Assert-MemberSet $contract.schemas.ResourceActionRequest.properties @('actionId', 'inputs', 'reason') 'ResourceActionRequest members changed'
Assert-Equal (@($contract.schemas.ResourceActionRequest.required).Count) 0 'Every ResourceActionRequest member is optional in the reference documentation'
Assert-True (Test-JsonMember $contract.schemas.ResourceAction.properties 'valid') 'ResourceAction must keep the valid flag the precheck depends on'

# ---------------------------------------------------------------- module ----

Import-Module -Name $ModulePath -Force
$exported = @(Get-Command -Module VcfAutomationDay2 -CommandType Function | Select-Object -ExpandProperty Name | Sort-Object)
Assert-Equal ($exported -join ',') 'Connect-VcfAutomationServer,Invoke-VcfAutomationResourceAction' 'Exported function set is incorrect'

$connectCommand = Get-Command Connect-VcfAutomationServer
foreach ($parameter in @('Server', 'AccessToken', 'Port', 'Protocol')) {
    Assert-True $connectCommand.Parameters.ContainsKey($parameter) "Connect-VcfAutomationServer is missing parameter $parameter"
}
$actionCommand = Get-Command Invoke-VcfAutomationResourceAction
foreach ($parameter in @('Connection', 'DeploymentId', 'ResourceName', 'ActionId', 'Reason', 'Inputs')) {
    Assert-True $actionCommand.Parameters.ContainsKey($parameter) "Invoke-VcfAutomationResourceAction is missing parameter $parameter"
}

# -------------------------------------------------------------- scenarios ---

$runDirectory = Join-Path ([IO.Path]::GetTempPath()) ("vcf91-0312-" + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $runDirectory

function Invoke-Scenario {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][hashtable]$Arguments
    )

    $scenarioDirectory = Join-Path $script:runDirectory $Name
    $null = New-Item -ItemType Directory -Path $scenarioDirectory
    $requestLog = Join-Path $scenarioDirectory 'requests.jsonl'
    $mutationLog = Join-Path $scenarioDirectory 'mutations.jsonl'
    $readyFile = Join-Path $scenarioDirectory 'ready.txt'
    $stdoutFile = Join-Path $scenarioDirectory 'mock.stdout'
    $stderrFile = Join-Path $scenarioDirectory 'mock.stderr'
    $mockProcess = $null
    $result = $null
    $connection = $null

    try {
        $mockProcess = Start-Process -FilePath 'python3' -ArgumentList @(
            $script:MockPath,
            '--contract', $script:ContractPath,
            '--log', $requestLog,
            '--mutations', $mutationLog,
            '--ready', $readyFile
        ) -PassThru -NoNewWindow -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile

        $deadline = [datetime]::UtcNow.AddSeconds(15)
        while (-not (Test-Path -LiteralPath $readyFile -PathType Leaf)) {
            if ($mockProcess.HasExited) {
                $mockError = if (Test-Path -LiteralPath $stderrFile) { Get-Content -LiteralPath $stderrFile -Raw } else { '' }
                throw "Loopback mock exited before becoming ready: $mockError"
            }
            if ([datetime]::UtcNow -ge $deadline) {
                throw 'Timed out waiting for the loopback mock to become ready.'
            }
            Start-Sleep -Milliseconds 50
        }

        $port = [int]((Get-Content -LiteralPath $readyFile -Raw).Trim())
        $secureToken = ConvertTo-SecureString $script:AccessTokenText -AsPlainText -Force
        $connection = Connect-VcfAutomationServer -Server '127.0.0.1' -AccessToken $secureToken -Port $port -Protocol 'http'
        Assert-True ($null -ne $connection) "[$Name] Connect-VcfAutomationServer returned no connection"

        $result = Invoke-VcfAutomationResourceAction -Connection $connection @Arguments
        Start-Sleep -Milliseconds 100
    }
    finally {
        if ($null -ne $mockProcess -and -not $mockProcess.HasExited) {
            Stop-Process -Id $mockProcess.Id -Force
            $mockProcess.WaitForExit()
        }
    }

    $requests = @(
        Get-Content -LiteralPath $requestLog |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json -Depth 40 }
    )
    $mutations = @(
        Get-Content -LiteralPath $mutationLog |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json -Depth 40 }
    )

    return [pscustomobject]@{
        Name       = $Name
        Connection = $connection
        Result     = $result
        Requests   = $requests
        Mutations  = $mutations
    }
}

function Assert-AuthenticatedRequest {
    param(
        [Parameter(Mandatory)][object]$Request,
        [Parameter(Mandatory)][string]$Label
    )
    Assert-Equal (Get-JsonMember $Request.headers 'authorization') "Bearer $script:AccessTokenText" "$Label Authorization header"
    $accept = Get-JsonMember $Request.headers 'accept'
    Assert-True ($null -ne $accept -and $accept -cmatch '(^|,\s*)application/json(\s*;|\s*,|$)') "$Label must send Accept: application/json"
}

function Assert-BodylessGet {
    param(
        [Parameter(Mandatory)][object]$Request,
        [Parameter(Mandatory)][string]$Label
    )
    Assert-Equal $Request.method 'GET' "$Label method"
    Assert-Equal $Request.bodyText '' "$Label must not send a body"
    Assert-True (-not (Test-JsonMember $Request.headers 'content-type')) "$Label must omit Content-Type on a bodyless GET"
    Assert-True (@($Request.headerNames) -notcontains 'transfer-encoding') "$Label must not use transfer encoding"
}

function Assert-ResourceLookup {
    param(
        [Parameter(Mandatory)][object]$Request,
        [Parameter(Mandatory)][string]$ResourceName,
        [Parameter(Mandatory)][string]$Label
    )
    Assert-Equal $Request.operationId 'getDeploymentResources' "$Label operation"
    Assert-Equal $Request.path "/deployment/api/deployments/$script:DeploymentId/resources" "$Label path"
    Assert-Equal (Get-JsonMember $Request.pathParameters 'deploymentId') $script:DeploymentId "$Label deploymentId path parameter"
    Assert-MemberSet $Request.queryParameters @('names') "$Label must send only the names filter and omit every unset optional query parameter"
    Assert-Equal (@(Get-JsonMember $Request.queryParameters 'names')).Count 1 "$Label names filter must carry one value"
    Assert-Equal (@(Get-JsonMember $Request.queryParameters 'names')[0]) $ResourceName "$Label names filter value"
    Assert-AuthenticatedRequest $Request $Label
    Assert-BodylessGet $Request $Label
}

function Assert-ActionLookup {
    param(
        [Parameter(Mandatory)][object]$Request,
        [Parameter(Mandatory)][string]$ResourceId,
        [Parameter(Mandatory)][string]$Label
    )
    Assert-Equal $Request.operationId 'getResourceActions' "$Label operation"
    Assert-Equal $Request.path "/deployment/api/resources/$ResourceId/actions" "$Label path"
    Assert-Equal (Get-JsonMember $Request.pathParameters 'resourceId') $ResourceId "$Label resourceId path parameter"
    Assert-Equal $Request.query '' "$Label must send no query string"
    Assert-AuthenticatedRequest $Request $Label
    Assert-BodylessGet $Request $Label
}

function Assert-ActionSubmission {
    param(
        [Parameter(Mandatory)][object]$Request,
        [Parameter(Mandatory)][string]$ResourceId,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$BodyMembers,
        [Parameter(Mandatory)][string]$Label
    )
    Assert-Equal $Request.operationId 'submitResourceActionRequest' "$Label operation"
    Assert-Equal $Request.method 'POST' "$Label method"
    Assert-Equal $Request.path "/deployment/api/resources/$ResourceId/requests" "$Label path"
    Assert-Equal (Get-JsonMember $Request.pathParameters 'resourceId') $ResourceId "$Label resourceId path parameter"
    Assert-Equal $Request.query '' "$Label must send no query string"
    Assert-AuthenticatedRequest $Request $Label
    $contentTypeValues = @(Get-JsonMember $Request.headerValues 'content-type')
    Assert-Equal $contentTypeValues.Count 1 "$Label must send exactly one Content-Type header"
    $contentType = $contentTypeValues[0]
    Assert-True ($null -ne $contentType -and $contentType -cmatch '^application/json(\s*;.*)?$') "$Label Content-Type must be application/json"
    Assert-True (@($Request.headerNames) -notcontains 'transfer-encoding') "$Label must not use transfer encoding"
    Assert-True ($null -ne $Request.json) "$Label body must be a JSON object"
    Assert-MemberOrder $Request.json $BodyMembers "$Label body must carry exactly the set members, in contract order, with every unset optional member omitted"
}

function Assert-Report {
    param(
        [Parameter(Mandatory)][object]$Result,
        [Parameter(Mandatory)][string]$Label
    )
    Assert-True ($null -ne $Result) "$Label returned no report"
    Assert-True ($Result -isnot [array]) "$Label must return exactly one report object"
    Assert-MemberOrder $Result @(
        'Outcome', 'DeploymentId', 'ResourceName', 'ResourceId',
        'ActionId', 'PrecheckStatus', 'PrecheckDetail', 'RequestId', 'RequestStatus'
    ) "$Label report property order"
    Assert-Equal $Result.DeploymentId $script:DeploymentId "$Label report DeploymentId"
}

function Assert-Blocked {
    param(
        [Parameter(Mandatory)][object]$Scenario,
        [Parameter(Mandatory)][string]$PrecheckStatus,
        [Parameter(Mandatory)][int]$ExpectedRequestCount
    )
    $label = "[$($Scenario.Name)]"
    Assert-Report $Scenario.Result $label
    Assert-Equal $Scenario.Result.Outcome 'Blocked' "$label outcome"
    Assert-Equal $Scenario.Result.PrecheckStatus $PrecheckStatus "$label precheck status"
    Assert-True (-not [string]::IsNullOrWhiteSpace($Scenario.Result.PrecheckDetail)) "$label must explain why the precheck blocked the action"
    Assert-Equal $Scenario.Result.RequestId $null "$label must not report a request id when the precheck fails"
    Assert-Equal $Scenario.Result.RequestStatus $null "$label must not report a request status when the precheck fails"
    Assert-Equal $Scenario.Requests.Count $ExpectedRequestCount "$label request count"
    $submissions = @($Scenario.Requests | Where-Object { $_.operationId -ceq 'submitResourceActionRequest' })
    Assert-Equal $submissions.Count 0 "$label must not submit the action request when the precheck fails"
    Assert-Equal $Scenario.Mutations.Count 0 "$label must leave the deployment unchanged when the precheck fails"
}

$scenarios = @{}

try {
    $scenarios['submit-minimal'] = Invoke-Scenario -Name 'submit-minimal' -Arguments @{
        DeploymentId = $DeploymentId
        ResourceName = 'web-01'
        ActionId     = 'Cloud.vSphere.Machine.PowerOff'
    }

    $scenarios['submit-full'] = Invoke-Scenario -Name 'submit-full' -Arguments @{
        DeploymentId = $DeploymentId
        ResourceName = 'web-01'
        ActionId     = 'Cloud.vSphere.Machine.Snapshot.Create'
        Reason       = 'Snapshot before the August guest patching window.'
        Inputs       = @{ name = 'pre-patch-2026-08-11'; description = 'Automated pre-patch snapshot'; memory = $false }
    }

    $scenarios['submit-reason-only'] = Invoke-Scenario -Name 'submit-reason-only' -Arguments @{
        DeploymentId = $DeploymentId
        ResourceName = 'web-01'
        ActionId     = 'Cloud.vSphere.Machine.PowerOff'
        Reason       = 'Planned application maintenance.'
    }

    $scenarios['submit-inputs-only'] = Invoke-Scenario -Name 'submit-inputs-only' -Arguments @{
        DeploymentId = $DeploymentId
        ResourceName = 'web-01'
        ActionId     = 'Cloud.vSphere.Machine.Snapshot.Create'
        Inputs       = @{ name = 'operator-checkpoint'; memory = $true }
    }

    $scenarios['submit-empty-inputs'] = Invoke-Scenario -Name 'submit-empty-inputs' -Arguments @{
        DeploymentId = $DeploymentId
        ResourceName = 'web-01'
        ActionId     = 'Cloud.vSphere.Machine.PowerOff'
        Inputs       = @{}
    }

    $scenarios['blocked-invalid-action'] = Invoke-Scenario -Name 'blocked-invalid-action' -Arguments @{
        DeploymentId = $DeploymentId
        ResourceName = 'web-01'
        ActionId     = 'Cloud.vSphere.Machine.Resize'
        Reason       = 'Right-size the front end.'
    }

    $scenarios['blocked-unavailable-action'] = Invoke-Scenario -Name 'blocked-unavailable-action' -Arguments @{
        DeploymentId = $DeploymentId
        ResourceName = 'web-01'
        ActionId     = 'Cloud.vSphere.Machine.Reboot'
    }

    $scenarios['blocked-missing-validity'] = Invoke-Scenario -Name 'blocked-missing-validity' -Arguments @{
        DeploymentId = $DeploymentId
        ResourceName = 'web-01'
        ActionId     = 'Cloud.vSphere.Machine.Restart'
    }

    $scenarios['blocked-missing-resource'] = Invoke-Scenario -Name 'blocked-missing-resource' -Arguments @{
        DeploymentId = $DeploymentId
        ResourceName = 'cache-01'
        ActionId     = 'Cloud.vSphere.Machine.PowerOff'
    }
}
finally {
    Remove-Module VcfAutomationDay2 -Force -ErrorAction SilentlyContinue
}

try {
    foreach ($scenario in $scenarios.Values) {
        $label = "[$($scenario.Name)]"
        Assert-True ($scenario.Requests.Count -gt 0) "$label reached the loopback mock with no request"
        $offContract = @($scenario.Requests | Where-Object { $null -eq $_.operationId })
        Assert-Equal $offContract.Count 0 "$label called a route outside the pinned contract"
        for ($index = 0; $index -lt $scenario.Requests.Count; $index++) {
            Assert-Equal $scenario.Requests[$index].sequence ($index + 1) "$label request sequence at index $index"
        }
        $tokenLeak = ($scenario.Connection | ConvertTo-Json -Depth 10 -Compress)
        Assert-True (-not $tokenLeak.Contains($AccessTokenText)) "$label connection object must not expose the access token in plain text"
        $reportLeak = ($scenario.Result | ConvertTo-Json -Depth 10 -Compress)
        Assert-True (-not $reportLeak.Contains($AccessTokenText)) "$label report must not expose the access token in plain text"
    }

    # -- the action is submitted with no optional member on the wire ---------
    $minimal = $scenarios['submit-minimal']
    Assert-Report $minimal.Result '[submit-minimal]'
    Assert-Equal $minimal.Requests.Count 3 '[submit-minimal] request count'
    Assert-ResourceLookup $minimal.Requests[0] 'web-01' '[submit-minimal] resource lookup'
    Assert-ActionLookup $minimal.Requests[1] 'res-web-01' '[submit-minimal] precheck'
    Assert-ActionSubmission $minimal.Requests[2] 'res-web-01' @('actionId') '[submit-minimal] submission'
    Assert-Equal $minimal.Requests[2].json.actionId 'Cloud.vSphere.Machine.PowerOff' '[submit-minimal] submitted actionId'
    Assert-Equal $minimal.Result.Outcome 'Submitted' '[submit-minimal] outcome'
    Assert-Equal $minimal.Result.PrecheckStatus 'Passed' '[submit-minimal] precheck status'
    Assert-Equal $minimal.Result.PrecheckDetail $null '[submit-minimal] a passing precheck reports no detail'
    Assert-Equal $minimal.Result.ResourceName 'web-01' '[submit-minimal] resource name'
    Assert-Equal $minimal.Result.ResourceId 'res-web-01' '[submit-minimal] resolved resource id'
    Assert-Equal $minimal.Result.ActionId 'Cloud.vSphere.Machine.PowerOff' '[submit-minimal] action id'
    Assert-Equal $minimal.Result.RequestId '9a3e0001-0f4c-4a1b-8c77-2d5e6f701001' '[submit-minimal] request id'
    Assert-Equal $minimal.Result.RequestStatus 'CREATED' '[submit-minimal] request status'
    Assert-Equal $minimal.Mutations.Count 1 '[submit-minimal] exactly one state change'
    Assert-Equal $minimal.Mutations[0].resourceId 'res-web-01' '[submit-minimal] mutated resource'

    # -- both optional members are set, in contract order --------------------
    $full = $scenarios['submit-full']
    Assert-Report $full.Result '[submit-full]'
    Assert-Equal $full.Requests.Count 3 '[submit-full] request count'
    Assert-ResourceLookup $full.Requests[0] 'web-01' '[submit-full] resource lookup'
    Assert-ActionLookup $full.Requests[1] 'res-web-01' '[submit-full] precheck'
    Assert-ActionSubmission $full.Requests[2] 'res-web-01' @('actionId', 'inputs', 'reason') '[submit-full] submission'
    $fullBody = $full.Requests[2].json
    Assert-Equal $fullBody.actionId 'Cloud.vSphere.Machine.Snapshot.Create' '[submit-full] submitted actionId'
    Assert-Equal $fullBody.reason 'Snapshot before the August guest patching window.' '[submit-full] submitted reason'
    Assert-MemberSet $fullBody.inputs @('name', 'description', 'memory') '[submit-full] submitted inputs member set'
    Assert-Equal $fullBody.inputs.name 'pre-patch-2026-08-11' '[submit-full] submitted input name'
    Assert-Equal $fullBody.inputs.description 'Automated pre-patch snapshot' '[submit-full] submitted input description'
    Assert-True ($fullBody.inputs.memory -is [bool]) '[submit-full] a boolean input must stay a JSON boolean'
    Assert-Equal $fullBody.inputs.memory $false '[submit-full] submitted input memory'
    Assert-Equal $full.Result.Outcome 'Submitted' '[submit-full] outcome'
    Assert-Equal $full.Result.RequestId '9a3e0002-0f4c-4a1b-8c77-2d5e6f701002' '[submit-full] request id'
    Assert-Equal $full.Mutations.Count 1 '[submit-full] exactly one state change'

    # -- each optional member is independently omitted -----------------------
    $reasonOnly = $scenarios['submit-reason-only']
    Assert-Report $reasonOnly.Result '[submit-reason-only]'
    Assert-Equal $reasonOnly.Requests.Count 3 '[submit-reason-only] request count'
    Assert-ActionSubmission $reasonOnly.Requests[2] 'res-web-01' @('actionId', 'reason') '[submit-reason-only] submission'
    Assert-Equal $reasonOnly.Requests[2].json.reason 'Planned application maintenance.' '[submit-reason-only] submitted reason'
    Assert-Equal $reasonOnly.Result.Outcome 'Submitted' '[submit-reason-only] outcome'
    Assert-Equal $reasonOnly.Mutations.Count 1 '[submit-reason-only] exactly one state change'

    $inputsOnly = $scenarios['submit-inputs-only']
    Assert-Report $inputsOnly.Result '[submit-inputs-only]'
    Assert-Equal $inputsOnly.Requests.Count 3 '[submit-inputs-only] request count'
    Assert-ActionSubmission $inputsOnly.Requests[2] 'res-web-01' @('actionId', 'inputs') '[submit-inputs-only] submission'
    Assert-MemberSet $inputsOnly.Requests[2].json.inputs @('name', 'memory') '[submit-inputs-only] submitted inputs member set'
    Assert-Equal $inputsOnly.Requests[2].json.inputs.name 'operator-checkpoint' '[submit-inputs-only] submitted input name'
    Assert-True ($inputsOnly.Requests[2].json.inputs.memory -is [bool]) '[submit-inputs-only] boolean input type'
    Assert-Equal $inputsOnly.Requests[2].json.inputs.memory $true '[submit-inputs-only] submitted input memory'
    Assert-Equal $inputsOnly.Result.Outcome 'Submitted' '[submit-inputs-only] outcome'
    Assert-Equal $inputsOnly.Mutations.Count 1 '[submit-inputs-only] exactly one state change'

    # -- an empty inputs table is still an unset optional member -------------
    $emptyInputs = $scenarios['submit-empty-inputs']
    Assert-Report $emptyInputs.Result '[submit-empty-inputs]'
    Assert-Equal $emptyInputs.Requests.Count 3 '[submit-empty-inputs] request count'
    Assert-ActionSubmission $emptyInputs.Requests[2] 'res-web-01' @('actionId') '[submit-empty-inputs] submission'
    Assert-Equal $emptyInputs.Result.Outcome 'Submitted' '[submit-empty-inputs] outcome'
    Assert-Equal $emptyInputs.Mutations.Count 1 '[submit-empty-inputs] exactly one state change'

    # -- the precheck gates the mutating call --------------------------------
    $invalidAction = $scenarios['blocked-invalid-action']
    Assert-Blocked $invalidAction 'ActionNotValid' 2
    Assert-ResourceLookup $invalidAction.Requests[0] 'web-01' '[blocked-invalid-action] resource lookup'
    Assert-ActionLookup $invalidAction.Requests[1] 'res-web-01' '[blocked-invalid-action] precheck'
    Assert-Equal $invalidAction.Result.ResourceId 'res-web-01' '[blocked-invalid-action] resolved resource id'
    Assert-Equal $invalidAction.Result.ResourceName 'web-01' '[blocked-invalid-action] resource name'
    Assert-Equal $invalidAction.Result.ActionId 'Cloud.vSphere.Machine.Resize' '[blocked-invalid-action] action id'

    $unavailableAction = $scenarios['blocked-unavailable-action']
    Assert-Blocked $unavailableAction 'ActionNotAvailable' 2
    Assert-ResourceLookup $unavailableAction.Requests[0] 'web-01' '[blocked-unavailable-action] resource lookup'
    Assert-ActionLookup $unavailableAction.Requests[1] 'res-web-01' '[blocked-unavailable-action] precheck'
    Assert-Equal $unavailableAction.Result.ResourceId 'res-web-01' '[blocked-unavailable-action] resolved resource id'
    Assert-Equal $unavailableAction.Result.ResourceName 'web-01' '[blocked-unavailable-action] resource name'
    Assert-Equal $unavailableAction.Result.ActionId 'Cloud.vSphere.Machine.Reboot' '[blocked-unavailable-action] action id'

    $missingValidity = $scenarios['blocked-missing-validity']
    Assert-Blocked $missingValidity 'ActionNotValid' 2
    Assert-ResourceLookup $missingValidity.Requests[0] 'web-01' '[blocked-missing-validity] resource lookup'
    Assert-ActionLookup $missingValidity.Requests[1] 'res-web-01' '[blocked-missing-validity] precheck'
    Assert-Equal $missingValidity.Result.ResourceId 'res-web-01' '[blocked-missing-validity] resolved resource id'
    Assert-Equal $missingValidity.Result.ResourceName 'web-01' '[blocked-missing-validity] resource name'
    Assert-Equal $missingValidity.Result.ActionId 'Cloud.vSphere.Machine.Restart' '[blocked-missing-validity] action id'

    $missingResource = $scenarios['blocked-missing-resource']
    Assert-Blocked $missingResource 'ResourceNotFound' 1
    Assert-ResourceLookup $missingResource.Requests[0] 'cache-01' '[blocked-missing-resource] resource lookup'
    Assert-Equal $missingResource.Result.ResourceId $null '[blocked-missing-resource] must report no resource id'
    Assert-Equal $missingResource.Result.ResourceName 'cache-01' '[blocked-missing-resource] resource name'
    Assert-Equal $missingResource.Result.ActionId 'Cloud.vSphere.Machine.PowerOff' '[blocked-missing-resource] action id'

    Write-Output 'PASS: precheck gating, exact VCF Automation wire contract, optional-member omission, and report shape are correct.'
}
finally {
    if (Test-Path -LiteralPath $runDirectory) {
        Remove-Item -LiteralPath $runDirectory -Recurse -Force
    }
}
