$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $Root (
    'VcfNsxPartialRollout/VcfNsxPartialRollout.psd1'
)
$ModulePath = Join-Path $Root (
    'VcfNsxPartialRollout/VcfNsxPartialRollout.psm1'
)
$ContractPath = Join-Path $Root 'docs/contract.json'
$SourcesPath = Join-Path $Root 'docs/official_sources.json'
$MockPath = Join-Path $PSScriptRoot 'mock_nsx_policy.py'
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    'vcf91-0053-' + [guid]::NewGuid().ToString('N')
)
[System.IO.Directory]::CreateDirectory($TempRoot) | Out-Null

function Assert-True {
    param(
        [Parameter(Mandatory)]
        [bool] $Condition,

        [Parameter(Mandatory)]
        [string] $Message
    )

    if (-not $Condition) {
        throw "ASSERTION FAILED: $Message"
    }
}

function Assert-Equal {
    param(
        [AllowNull()]
        $Actual,

        [AllowNull()]
        $Expected,

        [Parameter(Mandatory)]
        [string] $Message
    )

    if ($Actual -cne $Expected) {
        throw "ASSERTION FAILED: $Message (expected '$Expected', got '$Actual')"
    }
}

function Get-OneHeader {
    param(
        [Parameter(Mandatory)]
        $Entry,

        [Parameter(Mandatory)]
        [string] $Name
    )

    $Property = $Entry.headers.PSObject.Properties |
        Where-Object Name -CEQ $Name.ToLowerInvariant() |
        Select-Object -First 1
    Assert-True ($null -ne $Property) "$Name header is present"
    $Values = @($Property.Value)
    Assert-Equal $Values.Count 1 "$Name occurs exactly once"
    return [string] $Values[0]
}

function Get-ExpectedAuthorization {
    param(
        [Parameter(Mandatory)]
        [string] $Username,

        [Parameter(Mandatory)]
        [string] $Password
    )

    return 'Basic ' + [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes("${Username}:${Password}")
    )
}

function Get-RequestBody {
    param(
        [Parameter(Mandatory)]
        $Entry
    )

    $Bytes = [Convert]::FromBase64String(
        [string] $Entry.bodyBase64
    )
    Assert-True (
        $Bytes.Length -lt 3 -or
        -not (
            $Bytes[0] -eq 0xEF -and
            $Bytes[1] -eq 0xBB -and
            $Bytes[2] -eq 0xBF
        )
    ) 'request body has no UTF-8 BOM'
    $Text = [Text.Encoding]::UTF8.GetString($Bytes)
    return [pscustomobject]@{
        Bytes = $Bytes
        Text = $Text
        Json = $Text | ConvertFrom-Json -Depth 30
    }
}

function Assert-MemberSet {
    param(
        [Parameter(Mandatory)]
        $Object,

        [Parameter(Mandatory)]
        [string[]] $Expected,

        [Parameter(Mandatory)]
        [string] $Message
    )

    $Actual = @($Object.PSObject.Properties.Name | Sort-Object)
    $Wanted = @($Expected | Sort-Object)
    Assert-Equal ($Actual -join ',') ($Wanted -join ',') $Message
}

function Assert-CommonWire {
    param(
        [Parameter(Mandatory)]
        $Entry,

        [Parameter(Mandatory)]
        $Case,

        [Parameter(Mandatory)]
        [string] $OperationId,

        [Parameter(Mandatory)]
        [string] $ExpectedPath
    )

    Assert-Equal $Entry.operationId $OperationId `
        "$($Case.Name) operationId"
    Assert-Equal $Entry.method 'PATCH' "$($Case.Name) HTTP method"
    Assert-Equal $Entry.path $ExpectedPath "$($Case.Name) escaped path"
    Assert-Equal $Entry.rawQuery '' "$($Case.Name) empty query"
    Assert-Equal (Get-OneHeader $Entry 'authorization') (
        Get-ExpectedAuthorization `
            -Username $Case.Username `
            -Password $Case.Password
    ) "$($Case.Name) Basic authorization"
    $Accept = Get-OneHeader $Entry 'accept'
    Assert-True $Accept.StartsWith(
        'application/json',
        [StringComparison]::OrdinalIgnoreCase
    ) "$($Case.Name) JSON Accept header"
    $ContentType = Get-OneHeader $Entry 'content-type'
    Assert-True $ContentType.StartsWith(
        'application/json',
        [StringComparison]::OrdinalIgnoreCase
    ) "$($Case.Name) JSON Content-Type header"
    $Body = Get-RequestBody $Entry
    $Length = [int](Get-OneHeader $Entry 'content-length')
    Assert-Equal $Length $Body.Bytes.Length `
        "$($Case.Name) exact Content-Length"
    Assert-True ($Body.Bytes.Length -gt 0) "$($Case.Name) body present"
    return $Body
}

function Assert-GroupWire {
    param(
        [Parameter(Mandatory)]
        $Entry,

        [Parameter(Mandatory)]
        $Case
    )

    $Domain = [uri]::EscapeDataString($Case.DomainId)
    $Group = [uri]::EscapeDataString($Case.GroupId)
    $Body = Assert-CommonWire `
        -Entry $Entry `
        -Case $Case `
        -OperationId 'PatchGroupForDomain' `
        -ExpectedPath (
            "/policy/api/v1/infra/domains/$Domain/groups/$Group"
        )

    $Members = @('expression', 'resource_type', 'display_name')
    if ($Case.IncludeOptional) {
        $Members += 'description'
    }
    Assert-MemberSet $Body.Json $Members `
        "$($Case.Name) exact Group member set"
    Assert-Equal $Body.Json.resource_type 'Group' `
        "$($Case.Name) Group resource_type"
    Assert-Equal $Body.Json.display_name $Case.GroupDisplayName `
        "$($Case.Name) Group display_name"
    Assert-Equal @($Body.Json.expression).Count 1 `
        "$($Case.Name) one Group expression"

    $Expression = $Body.Json.expression[0]
    Assert-MemberSet $Expression @('ip_addresses', 'resource_type') `
        "$($Case.Name) exact IPAddressExpression member set"
    Assert-Equal $Expression.resource_type 'IPAddressExpression' `
        "$($Case.Name) expression resource_type"
    Assert-Equal (@($Expression.ip_addresses) -join '|') `
        ($Case.IpAddress -join '|') `
        "$($Case.Name) ordered IP addresses"
    if ($Case.IncludeOptional) {
        Assert-Equal $Body.Json.description $Case.GroupDescription `
            "$($Case.Name) Group description"
    }
    else {
        Assert-True (
            $Body.Json.PSObject.Properties.Name -cnotcontains 'description'
        ) "$($Case.Name) omits Group description"
    }
}

function Assert-PolicyWire {
    param(
        [Parameter(Mandatory)]
        $Entry,

        [Parameter(Mandatory)]
        $Case
    )

    $Domain = [uri]::EscapeDataString($Case.DomainId)
    $Policy = [uri]::EscapeDataString($Case.SecurityPolicyId)
    $Body = Assert-CommonWire `
        -Entry $Entry `
        -Case $Case `
        -OperationId 'PatchSecurityPolicyForDomain' `
        -ExpectedPath (
            "/policy/api/v1/infra/domains/$Domain/" +
            "security-policies/$Policy"
        )

    $Members = @(
        'rules',
        'sequence_number',
        'category',
        'stateful',
        'resource_type',
        'display_name'
    )
    if ($Case.IncludeOptional) {
        $Members += 'description'
    }
    Assert-MemberSet $Body.Json $Members `
        "$($Case.Name) exact SecurityPolicy member set"
    Assert-Equal $Body.Json.resource_type 'SecurityPolicy' `
        "$($Case.Name) policy resource_type"
    Assert-Equal $Body.Json.display_name $Case.PolicyDisplayName `
        "$($Case.Name) policy display_name"
    Assert-Equal $Body.Json.category 'Application' `
        "$($Case.Name) policy category"
    Assert-Equal ([uint32] $Body.Json.sequence_number) `
        $Case.PolicySequenceNumber `
        "$($Case.Name) policy sequence"
    Assert-Equal ([bool] $Body.Json.stateful) $true `
        "$($Case.Name) stateful policy"
    Assert-Equal @($Body.Json.rules).Count 1 `
        "$($Case.Name) one policy rule"

    $Rule = $Body.Json.rules[0]
    $RuleMembers = @(
        'action',
        'direction',
        'source_groups',
        'sequence_number',
        'services',
        'scope',
        'destination_groups',
        'resource_type',
        'display_name'
    )
    if ($Case.IncludeOptional) {
        $RuleMembers += 'notes'
    }
    Assert-MemberSet $Rule $RuleMembers `
        "$($Case.Name) exact Rule member set"
    Assert-Equal $Rule.resource_type 'Rule' `
        "$($Case.Name) Rule resource_type"
    Assert-Equal $Rule.display_name $Case.RuleDisplayName `
        "$($Case.Name) Rule display_name"
    Assert-Equal ([uint32] $Rule.sequence_number) `
        $Case.RuleSequenceNumber `
        "$($Case.Name) Rule sequence"
    Assert-Equal (@($Rule.source_groups) -join ',') (
        "/infra/domains/$($Case.DomainId)/groups/$($Case.GroupId)"
    ) "$($Case.Name) raw source intent path"
    Assert-Equal (@($Rule.destination_groups) -join ',') `
        $Case.DestinationGroupPath `
        "$($Case.Name) destination group"
    Assert-Equal (@($Rule.services) -join ',') 'ANY' `
        "$($Case.Name) services"
    Assert-Equal (@($Rule.scope) -join ',') 'ANY' `
        "$($Case.Name) scope"
    Assert-Equal $Rule.action 'ALLOW' "$($Case.Name) action"
    Assert-Equal $Rule.direction 'IN_OUT' "$($Case.Name) direction"

    if ($Case.IncludeOptional) {
        Assert-Equal $Body.Json.description $Case.PolicyDescription `
            "$($Case.Name) Policy description"
        Assert-Equal $Rule.notes $Case.RuleNotes `
            "$($Case.Name) Rule notes"
    }
    else {
        Assert-True (
            $Body.Json.PSObject.Properties.Name -cnotcontains 'description'
        ) "$($Case.Name) omits Policy description"
        Assert-True (
            $Rule.PSObject.Properties.Name -cnotcontains 'notes'
        ) "$($Case.Name) omits Rule notes"
    }
}

function Assert-Report {
    param(
        [Parameter(Mandatory)]
        $Case,

        [Parameter(Mandatory)]
        [string] $Status,

        [Parameter(Mandatory)]
        [int] $Succeeded,

        [Parameter(Mandatory)]
        [int] $Failed,

        [Parameter(Mandatory)]
        [int] $StepCount
    )

    Assert-True ($null -eq $Case.Error) "$($Case.Name) returns a report"
    Assert-True ($null -ne $Case.Result) "$($Case.Name) result is present"
    Assert-MemberSet $Case.Result @(
        'Status', 'Succeeded', 'Failed', 'Steps'
    ) "$($Case.Name) report member set"
    Assert-Equal (
        @($Case.Result.PSObject.Properties.Name) -join ','
    ) 'Status,Succeeded,Failed,Steps' `
        "$($Case.Name) report property order"
    Assert-Equal $Case.Result.Status $Status "$($Case.Name) report status"
    Assert-Equal ([int] $Case.Result.Succeeded) $Succeeded `
        "$($Case.Name) succeeded count"
    Assert-Equal ([int] $Case.Result.Failed) $Failed `
        "$($Case.Name) failed count"
    Assert-Equal @($Case.Result.Steps).Count $StepCount `
        "$($Case.Name) step count"
    Assert-True $Case.ReportExists "$($Case.Name) report file exists"

    $ExpectedText = (
        $Case.Result | ConvertTo-Json -Depth 10 -Compress
    ) + "`n"
    Assert-Equal $Case.ReportText $ExpectedText `
        "$($Case.Name) exact report bytes"
    Assert-True ($Case.ReportBytes.Length -gt 0) `
        "$($Case.Name) report is not empty"
    Assert-True (
        $Case.ReportBytes.Length -lt 3 -or
        -not (
            $Case.ReportBytes[0] -eq 0xEF -and
            $Case.ReportBytes[1] -eq 0xBB -and
            $Case.ReportBytes[2] -eq 0xBF
        )
    ) "$($Case.Name) report has no UTF-8 BOM"
    Assert-Equal $Case.ReportBytes[-1] 0x0A `
        "$($Case.Name) report ends in one LF"
    Assert-True (
        -not $Case.ReportText.Contains($Case.Username) -and
        -not $Case.ReportText.Contains($Case.Password) -and
        -not $Case.ReportText.Contains('Authorization')
    ) "$($Case.Name) report contains no credentials"
}

function Assert-ValidationFailure {
    param(
        [Parameter(Mandatory)]
        [hashtable] $Arguments,

        [Parameter(Mandatory)]
        [string] $ExpectedReportPath,

        [Parameter(Mandatory)]
        [string] $LogPath,

        [Parameter(Mandatory)]
        [string] $Message
    )

    $Before = @(
        if (Test-Path -LiteralPath $LogPath) {
            Get-Content -LiteralPath $LogPath |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        }
    ).Count
    $Thrown = $null
    try {
        Invoke-VcfNsxPartialRollout @Arguments | Out-Null
    }
    catch {
        $Thrown = $_.Exception
    }
    $After = @(
        if (Test-Path -LiteralPath $LogPath) {
            Get-Content -LiteralPath $LogPath |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        }
    ).Count

    Assert-True ($null -ne $Thrown) "$Message terminates"
    Assert-Equal $After $Before "$Message sends no request"
    Assert-True (-not (Test-Path -LiteralPath $ExpectedReportPath)) `
        "$Message creates no report"
}

function Invoke-RolloutCase {
    param(
        [Parameter(Mandatory)]
        [string] $Name,

        [Parameter(Mandatory)]
        [int] $GroupStatus,

        [Parameter(Mandatory)]
        [int] $PolicyStatus,

        [switch] $IncludeOptional,

        [switch] $RunValidationMatrix,

        [switch] $UseBoundaryValues,

        [switch] $OmitGroupErrorCode,

        [switch] $OmitPolicyErrorCode
    )

    $RunId = [guid]::NewGuid().ToString('N')
    $CaseRoot = Join-Path $TempRoot $Name
    [System.IO.Directory]::CreateDirectory($CaseRoot) | Out-Null
    $PortPath = Join-Path $CaseRoot 'port.txt'
    $LogPath = Join-Path $CaseRoot 'requests.jsonl'
    $ScenarioPath = Join-Path $CaseRoot 'scenario.json'
    $ReportPath = Join-Path $CaseRoot 'nested/report.json'
    $StdoutPath = Join-Path $CaseRoot 'mock.stdout'
    $StderrPath = Join-Path $CaseRoot 'mock.stderr'
    $MockProcess = $null
    $HttpClient = $null
    $HttpHandler = $null

    $Case = [ordered]@{
        Name = $Name
        Username = 'fixture-user-' + $RunId.Substring(0, 8)
        Password = 'fixture-pass-' + $RunId.Substring(8, 10)
        DomainId = 'domain ' + $RunId.Substring(2, 6) + '/west?'
        GroupId = 'source+' + $RunId.Substring(8, 6) + '/blue?'
        SecurityPolicyId = 'policy/' + $RunId.Substring(14, 6) + '?'
        GroupDisplayName = 'Source group ' + $RunId.Substring(20, 6)
        IpAddress = @('10.20.0.0/24', '2001:db8::/64')
        PolicyDisplayName = 'Application policy ' + $RunId.Substring(26, 6)
        RuleDisplayName = "Allow app`ntraffic"
        DestinationGroupPath = (
            '/infra/domains/default/groups/destination-' +
            $RunId.Substring(0, 6)
        )
        PolicySequenceNumber = [uint32] 120
        RuleSequenceNumber = [uint32] 10
        IncludeOptional = [bool] $IncludeOptional
        GroupDescription = 'source group description'
        PolicyDescription = 'policy description'
        RuleNotes = 'ticket notes'
    }
    if ($UseBoundaryValues) {
        $Case.GroupDisplayName = 'G' * 255
        $Case.IpAddress = @('10.20.0.1') * 25000
        $Case.PolicyDisplayName = 'P' * 255
        $Case.RuleDisplayName = 'R' * 255
        $Case.PolicySequenceNumber = [uint32] 999999
        $Case.RuleSequenceNumber = [uint32] 2147483647
        $Case.GroupDescription = 'G' * 1024
        $Case.PolicyDescription = 'P' * 1024
        $Case.RuleNotes = 'N' * 2048
    }

    $Scenario = [ordered]@{
        username = $Case.Username
        password = $Case.Password
        domain_id = $Case.DomainId
        group_id = $Case.GroupId
        security_policy_id = $Case.SecurityPolicyId
        group_status = $GroupStatus
        group_error_code = 72001
        group_include_error_code = -not $OmitGroupErrorCode
        policy_status = $PolicyStatus
        policy_error_code = 73001
        policy_include_error_code = -not $OmitPolicyErrorCode
    }
    [System.IO.File]::WriteAllText(
        $ScenarioPath,
        ($Scenario | ConvertTo-Json -Depth 5 -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )

    try {
        $MockProcess = Start-Process -FilePath 'python3' -ArgumentList @(
            '-B',
            $MockPath,
            $PortPath,
            $LogPath,
            $ContractPath,
            $ScenarioPath
        ) -PassThru -RedirectStandardOutput $StdoutPath `
          -RedirectStandardError $StderrPath

        $Deadline = [System.Diagnostics.Stopwatch]::StartNew()
        while (-not (Test-Path -LiteralPath $PortPath)) {
            if ($MockProcess.HasExited) {
                $MockError = Get-Content -Raw -LiteralPath $StderrPath
                throw "Loopback mock exited before startup: $MockError"
            }
            if ($Deadline.Elapsed.TotalSeconds -gt 10) {
                throw "Timed out waiting for loopback mock startup."
            }
            Start-Sleep -Milliseconds 25
        }
        $Port = [int](Get-Content -Raw -LiteralPath $PortPath)

        $Configuration = [VMware.Binding.OpenApi.Client.Configuration]::new()
        $Configuration.BasePath = "http://127.0.0.1:$Port/policy/api/v1"
        $Configuration.Username = $Case.Username
        $Configuration.Password = ConvertTo-SecureString `
            $Case.Password -AsPlainText -Force
        $HttpHandler = [System.Net.Http.HttpClientHandler]::new()
        $HttpClient = [System.Net.Http.HttpClient]::new(
            $HttpHandler,
            $false
        )
        $PolicyApi = [VMware.Bindings.Nsx.Policy.Api.PolicyApi]::new(
            $HttpClient,
            $Configuration,
            $HttpHandler
        )
        $DfwSecurityPolicyApi =
            [VMware.Bindings.Nsx.Policy.Api.DfwSecurityPolicyApi]::new(
                $HttpClient,
                $Configuration,
                $HttpHandler
            )

        $Arguments = @{
            PolicyApi = $PolicyApi
            DfwSecurityPolicyApi = $DfwSecurityPolicyApi
            DomainId = $Case.DomainId
            GroupId = $Case.GroupId
            SecurityPolicyId = $Case.SecurityPolicyId
            GroupDisplayName = $Case.GroupDisplayName
            IpAddress = $Case.IpAddress
            PolicyDisplayName = $Case.PolicyDisplayName
            RuleDisplayName = $Case.RuleDisplayName
            DestinationGroupPath = $Case.DestinationGroupPath
            PolicySequenceNumber = $Case.PolicySequenceNumber
            RuleSequenceNumber = $Case.RuleSequenceNumber
            ReportPath = $ReportPath
        }
        if ($IncludeOptional) {
            $Arguments.GroupDescription = $Case.GroupDescription
            $Arguments.PolicyDescription = $Case.PolicyDescription
            $Arguments.RuleNotes = $Case.RuleNotes
        }

        if ($RunValidationMatrix) {
            $ValidationIndex = 0
            foreach ($ParameterName in @(
                'DomainId',
                'GroupId',
                'SecurityPolicyId',
                'GroupDisplayName',
                'PolicyDisplayName',
                'RuleDisplayName',
                'DestinationGroupPath',
                'ReportPath'
            )) {
                $ValidationIndex++
                $Invalid = $Arguments.Clone()
                $ExpectedReportPath = Join-Path $CaseRoot (
                    "validation-$ValidationIndex.json"
                )
                $Invalid.ReportPath = $ExpectedReportPath
                $Invalid[$ParameterName] = ' '
                Assert-ValidationFailure `
                    -Arguments $Invalid `
                    -ExpectedReportPath $ExpectedReportPath `
                    -LogPath $LogPath `
                    -Message "$ParameterName rejects blank text"
            }

            foreach ($ParameterName in @(
                'GroupDisplayName',
                'PolicyDisplayName',
                'RuleDisplayName'
            )) {
                $ValidationIndex++
                $Invalid = $Arguments.Clone()
                $ExpectedReportPath = Join-Path $CaseRoot (
                    "validation-$ValidationIndex.json"
                )
                $Invalid.ReportPath = $ExpectedReportPath
                $Invalid[$ParameterName] = 'x' * 256
                Assert-ValidationFailure `
                    -Arguments $Invalid `
                    -ExpectedReportPath $ExpectedReportPath `
                    -LogPath $LogPath `
                    -Message "$ParameterName enforces 255 characters"
            }

            $InvalidAddressCases = [object[]]::new(3)
            $InvalidAddressCases[0] = [string[]] @()
            $InvalidAddressCases[1] =
                [string[]] (@('10.20.0.1') * 25001)
            $InvalidAddressCases[2] =
                [string[]] @('10.20.0.1', ' ')
            foreach ($InvalidAddresses in $InvalidAddressCases) {
                $ValidationIndex++
                $Invalid = $Arguments.Clone()
                $ExpectedReportPath = Join-Path $CaseRoot (
                    "validation-$ValidationIndex.json"
                )
                $Invalid.ReportPath = $ExpectedReportPath
                $Invalid.IpAddress = $InvalidAddresses
                Assert-ValidationFailure `
                    -Arguments $Invalid `
                    -ExpectedReportPath $ExpectedReportPath `
                    -LogPath $LogPath `
                    -Message "IpAddress validation case $ValidationIndex"
            }

            foreach ($SequenceCase in @(
                @('PolicySequenceNumber', [uint32] 1000000),
                @('RuleSequenceNumber', [uint32] 2147483648)
            )) {
                $ValidationIndex++
                $Invalid = $Arguments.Clone()
                $ExpectedReportPath = Join-Path $CaseRoot (
                    "validation-$ValidationIndex.json"
                )
                $Invalid.ReportPath = $ExpectedReportPath
                $Invalid[$SequenceCase[0]] = $SequenceCase[1]
                Assert-ValidationFailure `
                    -Arguments $Invalid `
                    -ExpectedReportPath $ExpectedReportPath `
                    -LogPath $LogPath `
                    -Message "$($SequenceCase[0]) enforces its maximum"
            }

            foreach ($OptionalCase in @(
                @('GroupDescription', ' '),
                @('GroupDescription', ('x' * 1025)),
                @('PolicyDescription', ' '),
                @('PolicyDescription', ('x' * 1025)),
                @('RuleNotes', ' '),
                @('RuleNotes', ('x' * 2049))
            )) {
                $ValidationIndex++
                $Invalid = $Arguments.Clone()
                $ExpectedReportPath = Join-Path $CaseRoot (
                    "validation-$ValidationIndex.json"
                )
                $Invalid.ReportPath = $ExpectedReportPath
                $Invalid[$OptionalCase[0]] = $OptionalCase[1]
                Assert-ValidationFailure `
                    -Arguments $Invalid `
                    -ExpectedReportPath $ExpectedReportPath `
                    -LogPath $LogPath `
                    -Message "$($OptionalCase[0]) validates when bound"
            }
        }

        $Result = $null
        $Thrown = $null
        try {
            $Result = Invoke-VcfNsxPartialRollout @Arguments
        }
        catch {
            $Thrown = $_.Exception
        }

        Start-Sleep -Milliseconds 50
        $Entries = @(
            Get-Content -LiteralPath $LogPath |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                ForEach-Object { $_ | ConvertFrom-Json -Depth 30 }
        )
        $ReportExists = Test-Path -LiteralPath $ReportPath
        $ReportText = if ($ReportExists) {
            [System.IO.File]::ReadAllText(
                $ReportPath,
                [System.Text.Encoding]::UTF8
            )
        }
        else {
            $null
        }
        $ReportBytes = if ($ReportExists) {
            [System.IO.File]::ReadAllBytes($ReportPath)
        }
        else {
            $null
        }

    }
    finally {
        if ($null -ne $HttpClient) {
            $HttpClient.Dispose()
        }
        if ($null -ne $HttpHandler) {
            $HttpHandler.Dispose()
        }
        if ($null -ne $MockProcess -and -not $MockProcess.HasExited) {
            Stop-Process -Id $MockProcess.Id -Force -ErrorAction SilentlyContinue
            $MockProcess.WaitForExit()
        }
    }

    $Output = [ordered]@{}
    foreach ($Key in $Case.Keys) {
        $Output[$Key] = $Case[$Key]
    }
    $Output.Result = $Result
    $Output.Error = $Thrown
    $Output.Entries = $Entries
    $Output.ReportExists = $ReportExists
    $Output.ReportText = $ReportText
    $Output.ReportBytes = $ReportBytes
    return [pscustomobject] $Output
}

function Assert-TransportFailure {
    param(
        [Parameter(Mandatory)]
        [string] $CaseRoot
    )

    $Configuration = [VMware.Binding.OpenApi.Client.Configuration]::new()
    $Configuration.BasePath = 'http://127.0.0.1:1/policy/api/v1'
    $HttpHandler = [System.Net.Http.HttpClientHandler]::new()
    $HttpClient = [System.Net.Http.HttpClient]::new(
        $HttpHandler,
        $false
    )
    try {
        $PolicyApi = [VMware.Bindings.Nsx.Policy.Api.PolicyApi]::new(
            $HttpClient,
            $Configuration,
            $HttpHandler
        )
        $DfwSecurityPolicyApi =
            [VMware.Bindings.Nsx.Policy.Api.DfwSecurityPolicyApi]::new(
                $HttpClient,
                $Configuration,
                $HttpHandler
            )
        $HttpClient.Dispose()
        $ReportPath = Join-Path $CaseRoot 'transport/report.json'
        $Thrown = $null
        try {
            Invoke-VcfNsxPartialRollout `
                -PolicyApi $PolicyApi `
                -DfwSecurityPolicyApi $DfwSecurityPolicyApi `
                -DomainId 'transport-domain' `
                -GroupId 'transport-group' `
                -SecurityPolicyId 'transport-policy' `
                -GroupDisplayName 'Transport group' `
                -IpAddress @('10.20.0.1') `
                -PolicyDisplayName 'Transport policy' `
                -RuleDisplayName 'Transport rule' `
                -DestinationGroupPath '/infra/domains/default/groups/dest' `
                -PolicySequenceNumber 1 `
                -RuleSequenceNumber 1 `
                -ReportPath $ReportPath |
                Out-Null
        }
        catch {
            $Thrown = $_.Exception
        }
        Assert-True ($null -ne $Thrown) 'transport failure terminates'
        Assert-True (-not (Test-Path -LiteralPath $ReportPath)) `
            'transport failure creates no report'
    }
    finally {
        $HttpClient.Dispose()
        $HttpHandler.Dispose()
    }
}

try {
    $Sources = Get-Content -Raw -LiteralPath $SourcesPath | ConvertFrom-Json
    $Contract = Get-Content -Raw -LiteralPath $ContractPath | ConvertFrom-Json
    $ExpectedCommit = '3949fc33339fc5ea1b77eadb258f1cf49aa88e26'
    $ExpectedSpec = 'specifications/nsx/openapi-2.0/nsx_policy_api.yaml'
    $ExpectedOperationIds = @(
        'PatchGroupForDomain',
        'PatchSecurityPolicyForDomain'
    )

    Assert-Equal $Sources.repository 'vmware/vcf-api-specs' `
        'official repository'
    Assert-Equal $Sources.repository_commit_sha $ExpectedCommit `
        'pinned VCF 9.1 commit'
    Assert-Equal $Sources.spec_path $ExpectedSpec `
        'official NSX Policy specification path'
    Assert-Equal $Sources.license 'Apache-2.0' 'source license'
    Assert-Equal $Contract.derived_from.repository_commit_sha $ExpectedCommit `
        'contract commit provenance'
    Assert-Equal $Contract.derived_from.spec_path $ExpectedSpec `
        'contract path provenance'
    Assert-Equal (
        @($Sources.operations.operationId) -join ','
    ) ($ExpectedOperationIds -join ',') 'source operationIds'
    Assert-Equal (
        @($Contract.operations.operationId) -join ','
    ) ($ExpectedOperationIds -join ',') 'contract operationIds'
    Assert-Equal @($Contract.operations).Count 2 'contract operation count'
    Assert-Equal $Contract.operations[0].method 'PATCH' 'group method'
    Assert-Equal $Contract.operations[1].method 'PATCH' 'policy method'
    Assert-Equal $Contract.operations[0].path `
        '/infra/domains/{domain-id}/groups/{group-id}' 'group path'
    Assert-Equal $Contract.operations[1].path `
        '/infra/domains/{domain-id}/security-policies/{security-policy-id}' `
        'policy path'
    foreach ($Operation in @($Sources.operations)) {
        Assert-Equal $Operation.repository_commit_sha $ExpectedCommit `
            "$($Operation.operationId) records commit"
        Assert-Equal $Operation.spec_path $ExpectedSpec `
            "$($Operation.operationId) records spec path"
    }
    Assert-Equal (
        $Contract.serialization_contract.unset_optional_properties
    ) 'omit' 'unset optional serialization'

    $Manifest = Import-PowerShellDataFile -LiteralPath $ManifestPath
    Assert-Equal @($Manifest.FunctionsToExport).Count 1 `
        'manifest export count'
    Assert-Equal $Manifest.FunctionsToExport[0] `
        'Invoke-VcfNsxPartialRollout' 'manifest export'
    Assert-Equal @($Manifest.RequiredModules).Count 1 `
        'required module count'
    Assert-Equal $Manifest.RequiredModules[0].ModuleName `
        'VMware.Sdk.Vcf.SddcManager' 'VCF SDK prerequisite'
    Assert-Equal ([version] $Manifest.RequiredModules[0].ModuleVersion) `
        ([version] '13.5.0.25380678') 'VCF SDK version'

    $SourceText = Get-Content -Raw -LiteralPath $ModulePath
    foreach ($Forbidden in @(
        'Invoke-RestMethod',
        'Invoke-WebRequest',
        'System.Net.Http',
        'HttpClient',
        'curl',
        'Start-Process'
    )) {
        Assert-True (-not $SourceText.Contains($Forbidden)) `
            "production module must not contain $Forbidden"
    }
    foreach ($Required in @(
        'PatchGroupForDomain',
        'PatchSecurityPolicyForDomain',
        'VMware.Bindings.Nsx.Policy.Api.DfwSecurityPolicyApi',
        'VMware.Bindings.Nsx.Policy.Model.Group',
        'VMware.Bindings.Nsx.Policy.Model.IPAddressExpression',
        'VMware.Bindings.Nsx.Policy.Model.SecurityPolicy',
        'VMware.Bindings.Nsx.Policy.Model.Rule'
    )) {
        Assert-True $SourceText.Contains($Required) `
            "production module uses $Required"
    }

    $Vendored = @(
        Get-ChildItem -LiteralPath $Root -Recurse -File |
            Where-Object {
                $_.Extension -in @('.dll', '.nupkg') -or
                $_.Name -match '^VMware\..*\.(psd1|psm1)$'
            }
    )
    Assert-Equal $Vendored.Count 0 'no VMware SDK is vendored'

    $Sdk = Get-Module -ListAvailable -Name VMware.Sdk.Vcf.SddcManager |
        Where-Object Version -GE ([version] '13.5.0.25380678') |
        Sort-Object Version -Descending |
        Select-Object -First 1
    Assert-True ($null -ne $Sdk) 'VCF PowerCLI 9.1 is installed'
    Import-Module $Sdk.Path -ErrorAction Stop
    Import-Module $ManifestPath -Force -ErrorAction Stop
    $Exports = @((Get-Command -Module VcfNsxPartialRollout).Name)
    Assert-Equal $Exports.Count 1 'runtime export count'
    Assert-Equal $Exports[0] 'Invoke-VcfNsxPartialRollout' `
        'runtime export'

    $Partial = Invoke-RolloutCase `
        -Name 'partial-failure' `
        -GroupStatus 200 `
        -PolicyStatus 503 `
        -RunValidationMatrix
    Assert-Equal @($Partial.Entries).Count 2 `
        'partial failure sends exactly two requests'
    Assert-GroupWire -Entry $Partial.Entries[0] -Case $Partial
    Assert-PolicyWire -Entry $Partial.Entries[1] -Case $Partial
    Assert-Report `
        -Case $Partial `
        -Status 'partial_failure' `
        -Succeeded 1 `
        -Failed 1 `
        -StepCount 2
    Assert-Equal $Partial.Result.Steps[0].Name 'source-group' `
        'partial first step name'
    Assert-Equal $Partial.Result.Steps[0].OperationId `
        'PatchGroupForDomain' 'partial first operationId'
    Assert-Equal $Partial.Result.Steps[0].Status 'succeeded' `
        'partial first status'
    Assert-Equal (
        @($Partial.Result.Steps[0].PSObject.Properties.Name) -join ','
    ) 'Name,OperationId,Status' 'successful step shape'
    Assert-Equal $Partial.Result.Steps[1].Name 'security-policy' `
        'partial failed step name'
    Assert-Equal $Partial.Result.Steps[1].OperationId `
        'PatchSecurityPolicyForDomain' 'partial failed operationId'
    Assert-Equal $Partial.Result.Steps[1].Status 'failed' `
        'partial failed status'
    Assert-Equal ([int] $Partial.Result.Steps[1].HttpStatus) 503 `
        'partial failed HTTP status'
    Assert-Equal ([int64] $Partial.Result.Steps[1].ErrorCode) 73001 `
        'partial failed NSX error_code'
    Assert-Equal (
        @($Partial.Result.Steps[1].PSObject.Properties.Name) -join ','
    ) 'Name,OperationId,Status,HttpStatus,ErrorCode' `
        'failed step property order'

    $FirstFailure = Invoke-RolloutCase `
        -Name 'first-failure' `
        -GroupStatus 503 `
        -PolicyStatus 200 `
        -OmitGroupErrorCode
    Assert-Equal @($FirstFailure.Entries).Count 1 `
        'first failure stops before policy'
    Assert-GroupWire -Entry $FirstFailure.Entries[0] -Case $FirstFailure
    Assert-Report `
        -Case $FirstFailure `
        -Status 'failed' `
        -Succeeded 0 `
        -Failed 1 `
        -StepCount 1
    Assert-Equal $FirstFailure.Result.Steps[0].OperationId `
        'PatchGroupForDomain' 'first failure operationId'
    Assert-Equal ([int] $FirstFailure.Result.Steps[0].HttpStatus) 503 `
        'first failure HTTP status'
    Assert-Equal (
        @($FirstFailure.Result.Steps[0].PSObject.Properties.Name) -join ','
    ) 'Name,OperationId,Status,HttpStatus' `
        'first failure omits absent error_code'

    $Success = Invoke-RolloutCase `
        -Name 'success-with-optionals' `
        -GroupStatus 200 `
        -PolicyStatus 200 `
        -IncludeOptional `
        -UseBoundaryValues
    Assert-Equal @($Success.Entries).Count 2 `
        'success sends exactly two requests'
    Assert-GroupWire -Entry $Success.Entries[0] -Case $Success
    Assert-PolicyWire -Entry $Success.Entries[1] -Case $Success
    Assert-Report `
        -Case $Success `
        -Status 'succeeded' `
        -Succeeded 2 `
        -Failed 0 `
        -StepCount 2
    Assert-Equal $Success.Result.Steps[1].Status 'succeeded' `
        'second success status'

    Assert-TransportFailure -CaseRoot $TempRoot

    Write-Output 'all checks passed'
}
finally {
    Remove-Module VcfNsxPartialRollout -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
}
