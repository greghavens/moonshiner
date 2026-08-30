#Requires -Version 7.2
<#
    Protected verification for the VcfOpsNotificationOnboarding module.

    Drives module/VcfOpsNotificationOnboarding against verification/contract_mock.py,
    a loopback mock whose route table is built from docs/contract.json. No live
    VMware endpoint is contacted. Every assertion is made either against the
    report the module returned or against the mock's recorded request log, so
    the exact wire shape of each request is checked rather than inferred.

    Exit code 0 means every assertion passed.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:MockScript = Join-Path $PSScriptRoot 'contract_mock.py'
$script:ContractPath = Join-Path $script:RepoRoot 'docs/contract.json'
$script:ModuleManifest = Join-Path $script:RepoRoot 'module/VcfOpsNotificationOnboarding/VcfOpsNotificationOnboarding.psd1'

# Must match the fixtures in verification/contract_mock.py.
$script:Username = 'svc-notify'
$script:Password = 'VMw@re123!Ops'
$script:AuthSource = 'vIDMAuthSource'
$script:SessionToken = '8f2c41d7a05b4e9ab6d33c7e15f08a24::9a1f'
$script:AssignedPluginId = '5d1b7f60-8a24-4c39-b0e7-2f96ac41d853'
$script:CriticalTemplateId = '3e6c9a71-4d02-4f18-b5ad-2c7e91f6b830'
$script:DigestRuleId = 'a2f47c98-6b31-4d05-8e7a-19c4f0b3d276'
$script:ExpectedRejection = "Notification rule 'vcfops-critical-oncall' was rejected: payload template 3e6c9a71-4d02-4f18-b5ad-2c7e91f6b830 is bound to outbound method StandardEmailPlugin but no delivery address property is configured on plugin 5d1b7f60-8a24-4c39-b0e7-2f96ac41d853."
$script:ExpectedMissingRule = 'No notification rule fixture is defined for this rule name.'

$script:Failures = [System.Collections.Generic.List[string]]::new()
$script:Checks = 0
$script:Case = '<none>'

# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

function Add-Failure([string] $Message) {
    $script:Failures.Add("[$script:Case] $Message")
}

function Assert-True([bool] $Condition, [string] $Message) {
    $script:Checks++
    if (-not $Condition) { Add-Failure $Message }
}

function Assert-Equal($Expected, $Actual, [string] $Message) {
    $script:Checks++
    $expectedText = if ($null -eq $Expected) { '<null>' } else { [string] $Expected }
    $actualText = if ($null -eq $Actual) { '<null>' } else { [string] $Actual }
    if ($expectedText -cne $actualText) {
        Add-Failure "$Message : expected '$expectedText', got '$actualText'"
    }
}

function Assert-Null($Actual, [string] $Message) {
    $script:Checks++
    if ($null -ne $Actual -and "$Actual" -ne '') {
        Add-Failure "$Message : expected null, got '$Actual'"
    }
}

function Assert-SetEqual([string[]] $Expected, [string[]] $Actual, [string] $Message) {
    $script:Checks++
    $e = @($Expected | Sort-Object) -join ', '
    $a = @($Actual | Sort-Object) -join ', '
    if ($e -cne $a) {
        Add-Failure "$Message : expected exactly [$e], got [$a]"
    }
}

# ---------------------------------------------------------------------------
# Mock lifecycle
# ---------------------------------------------------------------------------

function Start-ContractMock {
    $workDir = Join-Path ([System.IO.Path]::GetTempPath()) ("vcfops-verify-" + [guid]::NewGuid().ToString('n'))
    New-Item -ItemType Directory -Path $workDir -Force | Out-Null
    $logPath = Join-Path $workDir 'requests.jsonl'
    $portPath = Join-Path $workDir 'port.txt'

    $process = Start-Process -FilePath 'python3' -PassThru -NoNewWindow -ArgumentList @(
        '-B', $script:MockScript,
        '--contract', $script:ContractPath,
        '--log', $logPath,
        '--port-file', $portPath
    )

    $deadline = [datetime]::UtcNow.AddSeconds(30)
    while ([datetime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath $portPath) {
            $text = (Get-Content -LiteralPath $portPath -Raw).Trim()
            if ($text -match '^\d+$') {
                return [pscustomobject] @{
                    Process = $process
                    Port    = [int] $text
                    LogPath = $logPath
                    WorkDir = $workDir
                }
            }
        }
        if ($process.HasExited) {
            throw "contract_mock.py exited with code $($process.ExitCode) before it began listening."
        }
        Start-Sleep -Milliseconds 50
    }
    throw 'contract_mock.py did not report a listening port within 30 seconds.'
}

function Stop-ContractMock($Mock) {
    if (-not $Mock.Process.HasExited) {
        $Mock.Process.Kill()
        $Mock.Process.WaitForExit(10000) | Out-Null
    }
}

function Get-RequestLog($Mock) {
    if (-not (Test-Path -LiteralPath $Mock.LogPath)) { return @() }
    $lines = Get-Content -LiteralPath $Mock.LogPath | Where-Object { $_.Trim().Length -gt 0 }
    return @($lines | ForEach-Object { $_ | ConvertFrom-Json })
}

function Get-MemberNames($Object) {
    # Enumerated one at a time: under StrictMode, projecting .Name across an
    # empty property collection is an error rather than an empty result.
    if ($null -eq $Object) { return @() }
    return @($Object.PSObject.Properties | ForEach-Object { $_.Name })
}

function Get-Value($Object, [string] $Name) {
    # Absent members read as $null so a missing field is reported as a failed
    # assertion rather than as a thrown StrictMode error.
    if ($null -eq $Object) { return $null }
    $property = @($Object.PSObject.Properties | Where-Object { $_.Name -ceq $Name })
    if ($property.Count -ne 1) { return $null }
    return $property[0].Value
}

function Get-BodyValue($Entry, [string] $Name) {
    return Get-Value $Entry.body_json $Name
}

function Get-BodyKeys($Entry) {
    return Get-MemberNames $Entry.body_json
}

function Get-QueryKeys($Entry) {
    return Get-MemberNames $Entry.query
}

function Select-Operation($Log, [string] $OperationId) {
    return @($Log | Where-Object { $_.operation_id -eq $OperationId })
}

# ---------------------------------------------------------------------------
# Shared wire-shape assertions
# ---------------------------------------------------------------------------

function Assert-CommonWireShape($Log) {
    $offContract = @($Log | Where-Object { $_.off_contract })
    Assert-True ($offContract.Count -eq 0) (
        "the module called operations the contract does not name: " +
        (($offContract | ForEach-Object { "$($_.method) $($_.path)" }) -join '; '))

    foreach ($entry in $Log) {
        $headerNames = @(Get-MemberNames $entry.headers)

        if ($entry.operation_id -eq 'acquireToken') {
            Assert-True (-not ($headerNames -contains 'authorization')) (
                'acquireToken must not present an Authorization header; it is the operation that mints the token')
        } else {
            $auth = if ($headerNames -contains 'authorization') { $entry.headers.authorization } else { $null }
            Assert-Equal ('OpsToken ' + $script:SessionToken) $auth (
                "$($entry.operation_id) must carry the acquired session token in the Authorization header")
        }

        if ($entry.method -eq 'GET') {
            Assert-True ([string]::IsNullOrEmpty($entry.body_text)) (
                "$($entry.operation_id) is a bodyless GET but carried a request body")
            Assert-True (-not ($headerNames -contains 'content-type')) (
                "$($entry.operation_id) is a bodyless GET but declared a Content-Type")
        } else {
            $contentType = if ($headerNames -contains 'content-type') { [string] $entry.headers.'content-type' } else { '' }
            Assert-True ($contentType -like 'application/json*') (
                "$($entry.operation_id) must send a JSON media type, got '$contentType'")
        }

        if (@(Get-QueryKeys $entry).Count -eq 0) {
            Assert-True (-not $entry.has_query_delimiter) (
                "$($entry.operation_id) sent no query parameters but still emitted a '?' in the request target: $($entry.target)")
        }
    }
}

function Assert-OperationSequence($Log, [string[]] $Expected) {
    $actual = @($Log | ForEach-Object { if ($null -eq $_.operation_id) { "<off-contract:$($_.path)>" } else { $_.operation_id } })
    $script:Checks++
    if (($Expected -join ' -> ') -cne ($actual -join ' -> ')) {
        Add-Failure ("request sequence mismatch`n           expected: " +
            ($Expected -join ' -> ') + "`n           actual:   " + ($actual -join ' -> '))
    }
}

function Assert-AcquireTokenShape($Log, [bool] $ExpectAuthSource) {
    $requests = Select-Operation $Log 'acquireToken'
    Assert-Equal 1 $requests.Count 'the session must issue acquireToken exactly once'
    if ($requests.Count -ne 1) { return }

    # The handshake body is composed by Connect-VcfOpsServer, not by the module:
    # the SDK serializes `authSource` whether or not one was given. There is no
    # way around that and still be using the SDK -- every request builder takes
    # a VcfOpsServer and only that cmdlet makes one -- so what the module
    # decides here is whether a value goes in, and that is what this asserts.
    # The stricter rule, that an unset optional is absent rather than null,
    # still binds every request the module composes itself.
    $bodyKeys = @(Get-BodyKeys $requests[0])
    $unexpected = @($bodyKeys | Where-Object { $_ -notin @('username', 'password', 'authSource') })
    Assert-Equal 0 $unexpected.Count (
        "acquireToken must carry nothing but username, password and authSource, got [$($bodyKeys -join ', ')]")
    Assert-True ($bodyKeys -contains 'username' -and $bodyKeys -contains 'password') (
        "acquireToken must carry username and password, got [$($bodyKeys -join ', ')]")
    Assert-Equal $script:Username (Get-BodyValue $requests[0] 'username') 'acquireToken username'
    Assert-Equal $script:Password (Get-BodyValue $requests[0] 'password') 'acquireToken password'
    if ($ExpectAuthSource) {
        Assert-Equal $script:AuthSource (Get-BodyValue $requests[0] 'authSource') 'acquireToken authSource'
    } else {
        Assert-True ($null -eq (Get-BodyValue $requests[0] 'authSource')) (
            'acquireToken must carry no authSource value when the caller supplied none')
    }
}

function Assert-StepOutcome($Report, [string] $Name, [string] $OperationId, [string] $Outcome) {
    $step = @($Report.Steps | Where-Object { $_.Name -eq $Name })
    $script:Checks++
    if ($step.Count -ne 1) {
        Add-Failure "report.Steps must contain exactly one step named '$Name', found $($step.Count)"
        return
    }
    Assert-Equal $OperationId $step[0].OperationId "step '$Name' OperationId"
    Assert-Equal $Outcome $step[0].Outcome "step '$Name' Outcome"
}

function Assert-ReportShape($Report) {
    $script:Checks++
    if ($null -eq $Report) {
        Add-Failure 'New-VcfOpsNotificationBinding returned nothing; it must return a report describing what was applied'
        return $false
    }
    $expected = @('Succeeded', 'PluginTypeSupported', 'PluginId', 'TemplateId', 'RuleId',
        'OrphanedPluginId', 'FailedOperationId', 'FailureStatusCode', 'FailureMessage', 'Steps')
    $missing = @($expected | Where-Object { $Report.PSObject.Properties.Name -notcontains $_ })
    if ($missing.Count -gt 0) {
        Add-Failure "the report is missing required properties: $($missing -join ', ')"
        return $false
    }
    Assert-Equal 4 (@($Report.Steps).Count) 'the report must describe all four steps'
    $expectedOrder = 'VerifyPluginType -> CreatePlugin -> ResolveTemplate -> CreateRule'
    $actualOrder = @($Report.Steps | ForEach-Object { Get-Value $_ 'Name' }) -join ' -> '
    Assert-Equal $expectedOrder $actualOrder 'report.Steps must preserve the specified step order'
    return $true
}

function New-Session($Mock, [switch] $WithoutAuthSource, [switch] $WithoutSkipCertificateCheck) {
    $credential = [pscredential]::new(
        $script:Username,
        (ConvertTo-SecureString $script:Password -AsPlainText -Force))
    $parameters = @{
        Server     = '127.0.0.1'
        Port       = $Mock.Port
        Protocol   = 'http'
        Credential = $credential
    }
    if (-not $WithoutAuthSource) {
        $parameters['AuthSource'] = $script:AuthSource
    }
    if (-not $WithoutSkipCertificateCheck) {
        $parameters['SkipCertificateCheck'] = $true
    }
    return Connect-VcfOpsNotificationSession @parameters
}

# ---------------------------------------------------------------------------
# Case A - a later step fails and the earlier ones must be reported accurately
# ---------------------------------------------------------------------------

function Invoke-CaseA {
    $script:Case = 'A/partially-applied'
    $mock = Start-ContractMock
    try {
        $session = New-Session $mock
        $report = New-VcfOpsNotificationBinding `
            -Connection $session `
            -PluginName 'vcfops-oncall-email' `
            -PluginTypeId 'StandardEmailPlugin' `
            -ConfigValue ([ordered] @{ SMTP_HOST = 'smtp.corp.example.com'; SMTP_PORT = '25' }) `
            -RuleName 'vcfops-critical-oncall' `
            -TemplateName 'Critical Alert Email' `
            -Criticality @('CRITICAL', 'IMMEDIATE')
    } finally {
        Stop-ContractMock $mock
    }

    $log = Get-RequestLog $mock
    Assert-CommonWireShape $log
    Assert-AcquireTokenShape $log $true
    Assert-OperationSequence $log @(
        'acquireToken'
        'getCurrentVersionOfServer'
        'getAlertPluginTypes'
        'createAlertPlugin'
        'getNotificationTemplates'
        'createNotificationPluginRule'
    )

    # -- createAlertPlugin wire shape --------------------------------------
    $plugin = Select-Operation $log 'createAlertPlugin'
    if ($plugin.Count -eq 1) {
        $entry = $plugin[0]
        # name and pluginTypeId are the only required properties. description
        # was not supplied, so it must be absent rather than "" or null.
        # enabled is read-only and pluginId is update-only per the schema.
        Assert-SetEqual @('name', 'pluginTypeId', 'configValues') (Get-BodyKeys $entry) `
            'createAlertPlugin body must carry exactly the supplied properties; unset optional fields are omitted, not sent empty'
        Assert-Equal 'vcfops-oncall-email' (Get-BodyValue $entry 'name') 'createAlertPlugin name'
        Assert-Equal 'StandardEmailPlugin' (Get-BodyValue $entry 'pluginTypeId') 'createAlertPlugin pluginTypeId'
        $values = @(Get-BodyValue $entry 'configValues')
        Assert-Equal 2 $values.Count 'createAlertPlugin configValues count'
        if ($values.Count -eq 2) {
            Assert-SetEqual @('name', 'value') (@(Get-MemberNames $values[0])) `
                'each configValues element is a name-value pair with exactly name and value'
            $pairs = @($values | ForEach-Object { "$(Get-Value $_ 'name')=$(Get-Value $_ 'value')" }) -join ','
            Assert-Equal 'SMTP_HOST=smtp.corp.example.com,SMTP_PORT=25' $pairs 'createAlertPlugin configValues content'
        }
    }

    # -- getNotificationTemplates wire shape -------------------------------
    $templates = Select-Operation $log 'getNotificationTemplates'
    if ($templates.Count -eq 1) {
        $entry = $templates[0]
        # Only the requested filter travels. page and pageSize carry server-side
        # defaults and must not be transmitted just because they exist.
        Assert-SetEqual @('name') (Get-QueryKeys $entry) `
            'getNotificationTemplates must send only the name filter; unset optional query fields are omitted, not sent empty'
        Assert-Equal 'Critical Alert Email' (@(Get-Value $entry.query 'name')[0]) 'getNotificationTemplates name filter'
    }

    # -- createNotificationPluginRule wire shape ---------------------------
    $rule = Select-Operation $log 'createNotificationPluginRule'
    if ($rule.Count -eq 1) {
        $entry = $rule[0]
        # enabled and sendHeartbeat have server-side defaults and id is assigned
        # on create, so none of them belongs in the request.
        Assert-SetEqual @('name', 'pluginId', 'templateId', 'criticalities') (Get-BodyKeys $entry) `
            'createNotificationPluginRule body must carry exactly the supplied properties; unset optional fields are omitted, not sent empty'
        Assert-Equal 'vcfops-critical-oncall' (Get-BodyValue $entry 'name') 'createNotificationPluginRule name'
        Assert-Equal $script:AssignedPluginId (Get-BodyValue $entry 'pluginId') `
            'createNotificationPluginRule must reference the pluginId the server assigned in the preceding step'
        Assert-Equal $script:CriticalTemplateId (Get-BodyValue $entry 'templateId') `
            'createNotificationPluginRule must reference the templateId resolved in the preceding step'
        Assert-Equal 'CRITICAL,IMMEDIATE' (@(Get-BodyValue $entry 'criticalities') -join ',') 'createNotificationPluginRule criticalities'
    }

    # -- the report ---------------------------------------------------------
    if (Assert-ReportShape $report) {
        Assert-Equal $false $report.Succeeded 'a rejected rule means the change did not succeed'
        Assert-Equal $true $report.PluginTypeSupported 'the plugin type was supported'
        Assert-Equal $script:AssignedPluginId $report.PluginId 'the created pluginId must be reported'
        Assert-Equal $script:CriticalTemplateId $report.TemplateId 'the resolved templateId must be reported'
        Assert-Null $report.RuleId 'no rule was created, so RuleId must be null'
        Assert-Equal $script:AssignedPluginId $report.OrphanedPluginId `
            'the plugin created before the failure is still on the appliance and must be reported as orphaned'
        Assert-Equal 'createNotificationPluginRule' $report.FailedOperationId 'the failing operationId'
        Assert-Equal 422 $report.FailureStatusCode 'the declared 422 status must be reported verbatim'
        Assert-Equal $script:ExpectedRejection $report.FailureMessage `
            'the server-supplied rejection message must be reported, not a rewritten one'

        Assert-StepOutcome $report 'VerifyPluginType' 'getAlertPluginTypes' 'Succeeded'
        Assert-StepOutcome $report 'CreatePlugin' 'createAlertPlugin' 'Succeeded'
        Assert-StepOutcome $report 'ResolveTemplate' 'getNotificationTemplates' 'Succeeded'
        Assert-StepOutcome $report 'CreateRule' 'createNotificationPluginRule' 'Failed'
    }
}

# ---------------------------------------------------------------------------
# Case B - control: the whole change applies, and a skipped step is reported
#          as skipped rather than as done
# ---------------------------------------------------------------------------

function Invoke-CaseB {
    $script:Case = 'B/fully-applied'
    $mock = Start-ContractMock
    try {
        # Exercise the optional connection inputs in their omitted state.
        $session = New-Session $mock -WithoutAuthSource -WithoutSkipCertificateCheck
        $report = New-VcfOpsNotificationBinding `
            -Connection $session `
            -PluginName 'vcfops-digest-email' `
            -PluginTypeId 'StandardEmailPlugin' `
            -ConfigValue ([ordered] @{ SMTP_HOST = 'smtp.corp.example.com' }) `
            -PluginDescription 'Warning digest delivery target' `
            -RuleName 'vcfops-warning-digest' `
            -CollectorGroupId 'cg-7742'
    } finally {
        Stop-ContractMock $mock
    }

    $log = Get-RequestLog $mock
    Assert-CommonWireShape $log
    Assert-AcquireTokenShape $log $false
    # No -TemplateName was supplied, so getNotificationTemplates must not be
    # called at all: a skipped step issues no request.
    Assert-OperationSequence $log @(
        'acquireToken'
        'getCurrentVersionOfServer'
        'getAlertPluginTypes'
        'createAlertPlugin'
        'createNotificationPluginRule'
    )

    $plugin = Select-Operation $log 'createAlertPlugin'
    if ($plugin.Count -eq 1) {
        # description was supplied this time, so it must be present: the rule is
        # "omit what was not supplied", not "always omit".
        Assert-SetEqual @('name', 'pluginTypeId', 'configValues', 'description') (Get-BodyKeys $plugin[0]) `
            'createAlertPlugin body must include description when the caller supplied one'
        Assert-Equal 'Warning digest delivery target' (Get-BodyValue $plugin[0] 'description') 'createAlertPlugin description'
    }

    $rule = Select-Operation $log 'createNotificationPluginRule'
    if ($rule.Count -eq 1) {
        Assert-SetEqual @('name', 'pluginId', 'collectorGroupId') (Get-BodyKeys $rule[0]) `
            'createNotificationPluginRule must include collectorGroupId when supplied and omit all unset optional fields'
        Assert-Equal 'cg-7742' (Get-BodyValue $rule[0] 'collectorGroupId') 'createNotificationPluginRule collectorGroupId'
    }

    if (Assert-ReportShape $report) {
        Assert-Equal $true $report.Succeeded 'the whole change applied'
        Assert-Equal $script:AssignedPluginId $report.PluginId 'the created pluginId must be reported'
        Assert-Null $report.TemplateId 'no template was requested, so TemplateId must be null'
        Assert-Equal $script:DigestRuleId $report.RuleId 'the created ruleId must be reported'
        Assert-Null $report.OrphanedPluginId 'the change completed, so nothing is orphaned'
        Assert-Null $report.FailedOperationId 'nothing failed'
        Assert-Null $report.FailureStatusCode 'nothing failed'
        Assert-Null $report.FailureMessage 'nothing failed'

        Assert-StepOutcome $report 'VerifyPluginType' 'getAlertPluginTypes' 'Succeeded'
        Assert-StepOutcome $report 'CreatePlugin' 'createAlertPlugin' 'Succeeded'
        Assert-StepOutcome $report 'ResolveTemplate' 'getNotificationTemplates' 'Skipped'
        Assert-StepOutcome $report 'CreateRule' 'createNotificationPluginRule' 'Succeeded'
    }
}

# ---------------------------------------------------------------------------
# Case C - the first step fails, so nothing may be created and no later step
#          may be reported as attempted
# ---------------------------------------------------------------------------

function Invoke-CaseC {
    $script:Case = 'C/nothing-applied'
    $mock = Start-ContractMock
    try {
        $session = New-Session $mock
        $report = New-VcfOpsNotificationBinding `
            -Connection $session `
            -PluginName 'vcfops-pager' `
            -PluginTypeId 'PagerDutyPlugin' `
            -ConfigValue ([ordered] @{ ROUTING_KEY = 'abc123' }) `
            -RuleName 'vcfops-critical-oncall' `
            -TemplateName 'Critical Alert Email'
    } finally {
        Stop-ContractMock $mock
    }

    $log = Get-RequestLog $mock
    Assert-CommonWireShape $log
    Assert-AcquireTokenShape $log $true
    # The plugin type is not in the server's supported list, so the run must
    # stop before it changes anything.
    Assert-OperationSequence $log @(
        'acquireToken'
        'getCurrentVersionOfServer'
        'getAlertPluginTypes'
    )

    if (Assert-ReportShape $report) {
        Assert-Equal $false $report.Succeeded 'an unsupported plugin type means the change did not succeed'
        Assert-Equal $false $report.PluginTypeSupported 'PagerDutyPlugin is not in the server supported list'
        Assert-Null $report.PluginId 'nothing was created, so PluginId must be null'
        Assert-Null $report.TemplateId 'the template step was never reached'
        Assert-Null $report.RuleId 'no rule was created'
        Assert-Null $report.OrphanedPluginId 'nothing was created, so nothing is orphaned'
        Assert-Equal 'getAlertPluginTypes' $report.FailedOperationId 'the failing operationId'
        # getAlertPluginTypes answered 200; the type was simply absent from the
        # list. There is no HTTP failure, so no status code may be invented.
        Assert-Null $report.FailureStatusCode `
            'the request succeeded, so FailureStatusCode must stay null rather than be fabricated'
        Assert-True (-not [string]::IsNullOrWhiteSpace($report.FailureMessage)) `
            'the report must explain why the plugin type was rejected'
        Assert-True ($report.FailureMessage -like '*PagerDutyPlugin*') `
            'the failure message must name the rejected plugin type'

        Assert-StepOutcome $report 'VerifyPluginType' 'getAlertPluginTypes' 'Failed'
        Assert-StepOutcome $report 'CreatePlugin' 'createAlertPlugin' 'NotAttempted'
        Assert-StepOutcome $report 'ResolveTemplate' 'getNotificationTemplates' 'NotAttempted'
        Assert-StepOutcome $report 'CreateRule' 'createNotificationPluginRule' 'NotAttempted'
    }
}

# ---------------------------------------------------------------------------
# Case D - resolving a requested template requires exactly one match
# ---------------------------------------------------------------------------

function Invoke-CaseD {
    $script:Case = 'D/template-not-found'
    $mock = Start-ContractMock
    try {
        $session = New-Session $mock
        $report = New-VcfOpsNotificationBinding `
            -Connection $session `
            -PluginName 'vcfops-missing-template' `
            -PluginTypeId 'StandardEmailPlugin' `
            -ConfigValue ([ordered] @{ SMTP_HOST = 'smtp.corp.example.com' }) `
            -RuleName 'vcfops-never-created' `
            -TemplateName 'No Such Notification Template'
    } finally {
        Stop-ContractMock $mock
    }

    $log = Get-RequestLog $mock
    Assert-CommonWireShape $log
    Assert-AcquireTokenShape $log $true
    Assert-OperationSequence $log @(
        'acquireToken'
        'getCurrentVersionOfServer'
        'getAlertPluginTypes'
        'createAlertPlugin'
        'getNotificationTemplates'
    )

    $templates = Select-Operation $log 'getNotificationTemplates'
    if ($templates.Count -eq 1) {
        Assert-SetEqual @('name') (Get-QueryKeys $templates[0]) `
            'template resolution must use only the supplied name filter'
        Assert-Equal 'No Such Notification Template' (@(Get-Value $templates[0].query 'name')[0]) `
            'getNotificationTemplates missing-name filter'
    }

    if (Assert-ReportShape $report) {
        Assert-Equal $false $report.Succeeded 'zero template matches means the binding did not succeed'
        Assert-Equal $true $report.PluginTypeSupported 'the plugin type was supported'
        Assert-Equal $script:AssignedPluginId $report.PluginId 'the plugin created before template resolution must be reported'
        Assert-Null $report.TemplateId 'zero matches means no templateId was resolved'
        Assert-Null $report.RuleId 'the rule must not be attempted without exactly one template match'
        Assert-Equal $script:AssignedPluginId $report.OrphanedPluginId `
            'the plugin remains orphaned when template resolution fails'
        Assert-Equal 'getNotificationTemplates' $report.FailedOperationId 'the template resolution operation failed'
        Assert-Null $report.FailureStatusCode 'a zero-match 200 response has no HTTP failure status'
        Assert-True ($report.FailureMessage -like '*No Such Notification Template*') `
            'the failure message must name the unresolved template'

        Assert-StepOutcome $report 'VerifyPluginType' 'getAlertPluginTypes' 'Succeeded'
        Assert-StepOutcome $report 'CreatePlugin' 'createAlertPlugin' 'Succeeded'
        Assert-StepOutcome $report 'ResolveTemplate' 'getNotificationTemplates' 'Failed'
        Assert-StepOutcome $report 'CreateRule' 'createNotificationPluginRule' 'NotAttempted'
    }
}

# ---------------------------------------------------------------------------
# Case E - the other declared rule rejection (404) is also a reportable result
# ---------------------------------------------------------------------------

function Invoke-CaseE {
    $script:Case = 'E/declared-404'
    $mock = Start-ContractMock
    try {
        $session = New-Session $mock
        $report = New-VcfOpsNotificationBinding `
            -Connection $session `
            -PluginName 'vcfops-missing-rule' `
            -PluginTypeId 'StandardEmailPlugin' `
            -ConfigValue ([ordered] @{ SMTP_HOST = 'smtp.corp.example.com' }) `
            -RuleName 'vcfops-no-rule-fixture'
    } finally {
        Stop-ContractMock $mock
    }

    $log = Get-RequestLog $mock
    Assert-CommonWireShape $log
    Assert-AcquireTokenShape $log $true
    Assert-OperationSequence $log @(
        'acquireToken'
        'getCurrentVersionOfServer'
        'getAlertPluginTypes'
        'createAlertPlugin'
        'createNotificationPluginRule'
    )

    if (Assert-ReportShape $report) {
        Assert-Equal $false $report.Succeeded 'a declared 404 rule rejection means the binding did not succeed'
        Assert-Equal $true $report.PluginTypeSupported 'the plugin type was supported'
        Assert-Equal $script:AssignedPluginId $report.PluginId 'the created pluginId must be reported'
        Assert-Null $report.TemplateId 'template resolution was deliberately skipped'
        Assert-Null $report.RuleId 'the rejected rule has no id'
        Assert-Equal $script:AssignedPluginId $report.OrphanedPluginId `
            'the plugin remains orphaned after the declared 404'
        Assert-Equal 'createNotificationPluginRule' $report.FailedOperationId 'the failing operationId'
        Assert-Equal 404 $report.FailureStatusCode 'the declared 404 status must be reported verbatim'
        Assert-Equal $script:ExpectedMissingRule $report.FailureMessage `
            'the server-supplied 404 message must be reported verbatim'

        Assert-StepOutcome $report 'VerifyPluginType' 'getAlertPluginTypes' 'Succeeded'
        Assert-StepOutcome $report 'CreatePlugin' 'createAlertPlugin' 'Succeeded'
        Assert-StepOutcome $report 'ResolveTemplate' 'getNotificationTemplates' 'Skipped'
        Assert-StepOutcome $report 'CreateRule' 'createNotificationPluginRule' 'Failed'
    }
}

# ---------------------------------------------------------------------------
# Case F - the public contract is preserved and the module is SDK-driven
# ---------------------------------------------------------------------------

function Invoke-CaseF {
    $script:Case = 'F/public-contract-and-sdk'
    $sourcePath = Join-Path $script:RepoRoot 'module/VcfOpsNotificationOnboarding/VcfOpsNotificationOnboarding.psm1'
    $source = Get-Content -LiteralPath $sourcePath -Raw
    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseInput(
        $source, [ref] $tokens, [ref] $parseErrors)
    Assert-Equal 0 @($parseErrors).Count 'the module source must parse without PowerShell syntax errors'
    $invokedCommands = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst]
    }, $true) | ForEach-Object { $_.GetCommandName() } | Where-Object { $null -ne $_ })

    $forbidden = @(
        'Invoke-RestMethod'
        'Invoke-WebRequest'
        'System.Net.Http'
        'System.Net.WebClient'
        'HttpWebRequest'
    )
    foreach ($token in $forbidden) {
        Assert-True (-not ($source -match [regex]::Escape($token))) (
            "the module must issue requests through the VMware.Sdk.Vcf.Ops cmdlets, but it references $token")
    }

    $required = @(
        'Connect-VcfOpsServer'
        'Initialize-VcfOpsnamevalue'
        'Initialize-VcfOpsnotificationplugin'
        'Initialize-VcfOpsnotificationrule'
        'Invoke-VcfOpsGetAlertPluginTypes'
        'Invoke-VcfOpsCreateAlertPlugin'
        'Invoke-VcfOpsGetNotificationTemplates'
        'Invoke-VcfOpsCreateNotificationPluginRule'
    )
    foreach ($cmdlet in $required) {
        Assert-True ($invokedCommands -contains $cmdlet) (
            "the module must invoke $cmdlet, rather than merely mention it")
    }

    $expectedParameters = @{
        'Connect-VcfOpsNotificationSession' = [ordered] @{
            Server               = [string]
            Credential           = [pscredential]
            Port                 = [int]
            Protocol             = [string]
            AuthSource           = [string]
            SkipCertificateCheck = [switch]
        }
        'New-VcfOpsNotificationBinding' = [ordered] @{
            Connection       = [psobject]
            PluginName       = [string]
            PluginTypeId     = [string]
            ConfigValue      = [System.Collections.IDictionary]
            RuleName         = [string]
            PluginDescription = [string]
            TemplateName     = [string]
            CollectorGroupId = [string]
            Criticality      = [string[]]
        }
    }
    foreach ($functionName in $expectedParameters.Keys) {
        $command = Get-Command $functionName -CommandType Function
        foreach ($parameterName in $expectedParameters[$functionName].Keys) {
            Assert-True ($command.Parameters.ContainsKey($parameterName)) `
                "$functionName must preserve parameter -$parameterName"
            if ($command.Parameters.ContainsKey($parameterName)) {
                Assert-Equal $expectedParameters[$functionName][$parameterName].FullName `
                    $command.Parameters[$parameterName].ParameterType.FullName `
                    "$functionName -$parameterName parameter type"
            }
        }
    }
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if (-not (Get-Command python3 -ErrorAction SilentlyContinue)) {
    Write-Error 'python3 is required to run the loopback contract mock.'
    exit 2
}
if (-not (Get-Module -ListAvailable -Name 'VMware.Sdk.Vcf.Ops')) {
    Write-Error 'VMware.Sdk.Vcf.Ops is a prerequisite and must be installed by the environment.'
    exit 2
}

Import-Module $script:ModuleManifest -Force -WarningAction SilentlyContinue -ErrorAction Stop

foreach ($case in 'Invoke-CaseA', 'Invoke-CaseB', 'Invoke-CaseC', 'Invoke-CaseD', 'Invoke-CaseE', 'Invoke-CaseF') {
    try {
        & $case
    } catch {
        $script:Checks++
        Add-Failure "the case threw instead of returning a report: $($_.Exception.Message)"
    }
}

$script:Case = 'summary'
Write-Host ''
if ($script:Failures.Count -eq 0) {
    Write-Host "PASS - $script:Checks assertions across 6 cases." -ForegroundColor Green
    exit 0
}

Write-Host "FAIL - $($script:Failures.Count) of $script:Checks assertions failed:" -ForegroundColor Red
foreach ($failure in $script:Failures) {
    Write-Host "  - $failure" -ForegroundColor Red
}
exit 1
