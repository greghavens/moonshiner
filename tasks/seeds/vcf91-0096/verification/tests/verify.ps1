Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

function Assert-True {
    param(
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
        throw (
            "ASSERTION FAILED: {0}. Expected <{1}> but got <{2}>." -f
            $Message,
            $Expected,
            $Actual
        )
    }
}

function Assert-JsonEqual {
    param(
        [Parameter(Mandatory)]
        $Actual,

        [Parameter(Mandatory)]
        $Expected,

        [Parameter(Mandatory)]
        [string] $Message
    )

    $ActualJson = $Actual | ConvertTo-Json -Compress -Depth 40
    $ExpectedJson = $Expected | ConvertTo-Json -Compress -Depth 40
    Assert-Equal $ActualJson $ExpectedJson $Message
}

function ConvertTo-TestBase64 {
    param(
        [Parameter(Mandatory)]
        [string] $Value
    )

    return [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes($Value)
    )
}

function Get-RequestHeaderValues {
    param(
        [Parameter(Mandatory)]
        [psobject] $Request,

        [Parameter(Mandatory)]
        [string] $Name
    )

    return @(
        $Request.headers |
            Where-Object {
                [string]::Equals(
                    [string] $_.name,
                    $Name,
                    [StringComparison]::OrdinalIgnoreCase
                )
            } |
            ForEach-Object { [string] $_.value }
    )
}

$FilesRoot = Split-Path -Parent $PSScriptRoot
$ContractPath = Join-Path $FilesRoot 'docs/contract.json'
$SourcesPath = Join-Path $FilesRoot 'docs/official_sources.json'
$ModuleRoot = Join-Path $FilesRoot 'VcfVcenterTpmEvidence'
$ManifestPath = Join-Path $ModuleRoot 'VcfVcenterTpmEvidence.psd1'
$ModulePath = Join-Path $ModuleRoot 'VcfVcenterTpmEvidence.psm1'
$MockPath = Join-Path $PSScriptRoot 'mock_vcenter.py'

$ListOperation = 'Vcenter.TrustedInfrastructure.Hosts.Hardware.Tpm_list'
$EventOperation = (
    'Vcenter.TrustedInfrastructure.Hosts.Hardware.Tpm.EventLog_get'
)
$ExpectedOperationIds = @($ListOperation, $EventOperation)
$ExpectedListCmdlet = (
    'Invoke-VcenterTrustedInfrastructureHostsHardwareTpmList'
)
$ExpectedEventCmdlet = (
    'Invoke-VcenterTrustedInfrastructureHostsHardwareTpmEventLogGet'
)

$Contract = Get-Content -Raw -LiteralPath $ContractPath | ConvertFrom-Json
$Sources = Get-Content -Raw -LiteralPath $SourcesPath | ConvertFrom-Json
Assert-Equal $Contract.openapi '3.0.3' 'contract OpenAPI version'
Assert-Equal $Contract.info.title 'vSphere Automation API' 'contract title'
Assert-Equal $Contract.info.version '9.1.0.0' 'contract API version'
Assert-Equal $Contract.server_base_path '/api' 'contract server base'
Assert-Equal (
    $Contract.source.spec_path
) (
    'specifications/vsphere/openapi/automation/vcenter.yaml'
) 'contract specification path'
Assert-Equal (
    $Contract.source.repository_commit_sha
) (
    '3949fc33339fc5ea1b77eadb258f1cf49aa88e26'
) 'contract repository commit'
Assert-Equal (
    $Contract.source.spec_blob_sha
) (
    '8028b0824c4ff3503d05f44814f967938a795c40'
) 'contract specification blob'
Assert-Equal $Sources.repository 'vmware/vcf-api-specs' 'official repository'
Assert-Equal (
    $Sources.repository_commit_sha
) (
    $Contract.source.repository_commit_sha
) 'contract and provenance commits agree'
Assert-Equal $Sources.spec_path $Contract.source.spec_path (
    'contract and provenance specification paths agree'
)
Assert-Equal @($Sources.operationIds).Count 2 (
    'official operationId count'
)
Assert-Equal (
    @($Sources.operationIds) -join ','
) (
    $ExpectedOperationIds -join ','
) 'official operationIds and order'

$Operations = @($Contract.operations)
Assert-Equal $Operations.Count 2 'contract operation count'
Assert-Equal (
    @($Operations.operationId) -join ','
) (
    $ExpectedOperationIds -join ','
) 'contract operationIds and order'
$ListContract = $Operations[0]
$EventContract = $Operations[1]
Assert-Equal $ListContract.sdkCmdlet $ExpectedListCmdlet (
    'list SDK cmdlet'
)
Assert-Equal $EventContract.sdkCmdlet $ExpectedEventCmdlet (
    'event-log SDK cmdlet'
)
Assert-Equal $ListContract.method 'GET' 'list method'
Assert-Equal $EventContract.method 'GET' 'event-log method'
Assert-Equal $ListContract.path (
    '/api/vcenter/trusted-infrastructure/hosts/{host}/hardware/tpm'
) 'list path'
Assert-Equal $EventContract.path (
    '/api/vcenter/trusted-infrastructure/hosts/{host}/hardware/tpm/{tpm}/event-log'
) 'event-log path'
Assert-Equal (
    @($ListContract.parameters.name) -join ','
) 'host,filter,major_versions' 'list parameter projection'
Assert-True (
    [bool] $ListContract.parameters[1].omitWhenUnset
) 'filter is omitted when unset'
Assert-True (
    [bool] $ListContract.parameters[2].omitWhenUnset
) 'major_versions is omitted when unset'
Assert-Equal (
    $Contract.schemas.`
        'Vcenter.TrustedInfrastructure.Hosts.Hardware.Tpm.FilterSpec'.`
        properties.active.type
) 'boolean' 'filter.active schema'

$Manifest = Import-PowerShellDataFile -LiteralPath $ManifestPath
Assert-Equal @($Manifest.RequiredModules).Count 1 (
    'manifest prerequisite count'
)
Assert-Equal (
    $Manifest.RequiredModules[0].ModuleName
) 'VMware.Sdk.vSphere' 'manifest VCF PowerCLI SDK prerequisite'
Assert-Equal (
    [version] $Manifest.RequiredModules[0].ModuleVersion
) (
    [version] '13.5.0.25380678'
) 'manifest VCF PowerCLI SDK version'
Assert-Equal (
    @($Manifest.FunctionsToExport) -join ','
) (
    'New-VcfVcenterTpmEvidenceClient,Get-VcfHostTpmFailureEvidence'
) 'manifest exports'

$Tokens = $null
$ParseErrors = $null
$Ast = [Management.Automation.Language.Parser]::ParseFile(
    $ModulePath,
    [ref] $Tokens,
    [ref] $ParseErrors
)
Assert-Equal @($ParseErrors).Count 0 'module parses without errors'
$SourceText = Get-Content -Raw -LiteralPath $ModulePath
$CommandAsts = @(
    $Ast.FindAll(
        {
            param($Node)
            $Node -is [Management.Automation.Language.CommandAst]
        },
        $true
    )
)
$CommandNames = @($CommandAsts | ForEach-Object { $_.GetCommandName() })
$FunctionNames = @(
    $Ast.FindAll(
        {
            param($Node)
            $Node -is [Management.Automation.Language.FunctionDefinitionAst]
        },
        $true
    ) |
        ForEach-Object { $_.Name }
)
foreach ($RequiredText in @(
    'VMware.Sdk.vSphere',
    $ExpectedListCmdlet,
    $ExpectedEventCmdlet
)) {
    Assert-True (
        $SourceText.IndexOf(
            $RequiredText,
            [StringComparison]::OrdinalIgnoreCase
        ) -ge 0
    ) (
        "production module must name $RequiredText"
    )
}
Assert-True ($CommandNames -contains 'Import-Module') (
    'production path lazily imports the SDK'
)
$ImportAsts = @(
    $CommandAsts |
        Where-Object { $_.GetCommandName() -eq 'Import-Module' }
)
Assert-True (
    @(
        $ImportAsts |
            Where-Object {
                $_.Extent.Text.IndexOf(
                    'VMware.Sdk.vSphere',
                    [StringComparison]::OrdinalIgnoreCase
                ) -ge 0
            }
    ).Count -ge 1
) 'production path imports VMware.Sdk.vSphere'
foreach ($ForbiddenText in @(
    'Invoke-RestMethod',
    'Invoke-WebRequest',
    'System.Net.Http',
    'Net.Http.HttpClient',
    'System.Net.Sockets',
    'Net.Sockets.Socket',
    'Diagnostics.Process',
    'Install-Module',
    'Save-Module',
    'Update-Module',
    'curl',
    'wget',
    'Start-Process',
    'TcpClient',
    'UdpClient',
    'WebClient'
)) {
    Assert-True (
        $SourceText.IndexOf(
            $ForbiddenText,
            [StringComparison]::OrdinalIgnoreCase
        ) -lt 0
    ) (
        "production module must not contain $ForbiddenText"
    )
}
foreach ($ForbiddenCommand in @(
    'irm',
    'iwr'
)) {
    Assert-True ($CommandNames -notcontains $ForbiddenCommand) (
        "production module must not invoke $ForbiddenCommand"
    )
}
foreach ($SdkCommand in @($ExpectedListCmdlet, $ExpectedEventCmdlet)) {
    Assert-True ($FunctionNames -notcontains $SdkCommand) (
        "production module must not redefine $SdkCommand"
    )
}
$ExpectedSdkParameters = [ordered]@{
    $ExpectedListCmdlet = @('Host', 'Server')
    $ExpectedEventCmdlet = @('Host', 'Server', 'Tpm')
}
foreach ($SdkCommand in @($ExpectedListCmdlet, $ExpectedEventCmdlet)) {
    $SdkInvocations = @(
        $CommandAsts |
            Where-Object { $_.GetCommandName() -eq $SdkCommand }
    )
    Assert-Equal $SdkInvocations.Count 1 (
        "production path invokes $SdkCommand exactly once"
    )
    $SdkParameters = @(
        $SdkInvocations[0].CommandElements |
            Where-Object {
                $_ -is [Management.Automation.Language.CommandParameterAst]
            } |
            ForEach-Object { $_.ParameterName } |
            Where-Object { $_ -cne 'ErrorAction' } |
            Sort-Object
    )
    Assert-Equal (
        $SdkParameters -join ','
    ) (
        @($ExpectedSdkParameters[$SdkCommand] | Sort-Object) -join ','
    ) "$SdkCommand parameter set"
}

Import-Module -Name $ModulePath -Force -ErrorAction Stop
$Exports = @(
    Get-Command -Module VcfVcenterTpmEvidence -CommandType Function |
        Sort-Object Name |
        ForEach-Object Name
)
Assert-Equal (
    $Exports -join ','
) (
    'Get-VcfHostTpmFailureEvidence,New-VcfVcenterTpmEvidenceClient'
) 'runtime exports'

function Invoke-SeamValidation {
    param(
        [AllowNull()]
        $ListResponse,

        [AllowNull()]
        $EventResponse,

        [bool] $ThrowList = $false,

        [bool] $ThrowEvent = $false,

        [string] $HostId = 'host-exact',

        [string] $TpmId = 'tpm-exact',

        [string] $TransportSecret = 'transport-secret-value'
    )

    $State = [pscustomobject]@{
        ListCalls = 0
        EventCalls = 0
    }
    $ListResponseValue = $ListResponse
    $EventResponseValue = $EventResponse
    $ThrowListValue = $ThrowList
    $ThrowEventValue = $ThrowEvent
    $SecretValue = $TransportSecret
    $OperationInvoker = {
        param(
            [string] $OperationId,
            [hashtable] $Parameters,
            $SdkServer
        )

        if ($OperationId -ceq $ListOperation) {
            $State.ListCalls += 1
            if ($ThrowListValue) {
                throw (
                    "nested transport message $SecretValue response body"
                )
            }
            Write-Output -NoEnumerate $ListResponseValue
            return
        }
        if ($OperationId -ceq $EventOperation) {
            $State.EventCalls += 1
            if ($ThrowEventValue) {
                throw (
                    "nested transport message $SecretValue response body"
                )
            }
            Write-Output -NoEnumerate $EventResponseValue
            return
        }
        throw "unexpected operation $OperationId"
    }.GetNewClosure()
    $ServerHandle = [pscustomobject]@{
        Credential = $TransportSecret
        Session = "session-$TransportSecret"
    }
    $Client = New-VcfVcenterTpmEvidenceClient `
        -Server $ServerHandle `
        -OperationInvoker $OperationInvoker
    $Result = $null
    $FailureMessage = $null
    $FailureRendering = $null
    try {
        $Result = Get-VcfHostTpmFailureEvidence `
            -Client $Client `
            -HostId $HostId `
            -TpmId $TpmId
    }
    catch {
        $FailureMessage = $_.Exception.Message
        $FailureRendering = @(
            $_.ToString()
            $_.Exception.ToString()
            $_.ScriptStackTrace
        ) -join "`n"
    }
    return [pscustomobject]@{
        Result = $Result
        FailureMessage = $FailureMessage
        FailureRendering = $FailureRendering
        ListCalls = $State.ListCalls
        EventCalls = $State.EventCalls
    }
}

$ConstructionState = [pscustomobject]@{ Calls = 0 }
$ConstructionInvoker = {
    param($OperationId, $Parameters, $SdkServer)

    $ConstructionState.Calls += 1
    throw 'construction must not dispatch'
}.GetNewClosure()
$ConstructionServer = [pscustomobject]@{
    RuntimeHandle = [guid]::NewGuid().ToString('N')
}
$ConstructionClientOne = New-VcfVcenterTpmEvidenceClient `
    -Server $ConstructionServer `
    -OperationInvoker $ConstructionInvoker
$ConstructionClientTwo = New-VcfVcenterTpmEvidenceClient `
    -Server $ConstructionServer `
    -OperationInvoker $ConstructionInvoker
Assert-Equal $ConstructionState.Calls 0 (
    'client construction performs no dispatch'
)
Assert-True (
    -not [object]::ReferenceEquals(
        $ConstructionClientOne,
        $ConstructionClientTwo
    )
) 'each client construction returns an independent client'

$NullClientFailed = $false
try {
    [void] (Get-VcfHostTpmFailureEvidence `
        -Client $null `
        -HostId 'host-exact' `
        -TpmId 'tpm-exact')
}
catch {
    $NullClientFailed = $true
}
Assert-True $NullClientFailed 'null client is rejected'

$PascalSummary = [pscustomobject] [ordered]@{
    Tpm = 'tpm-exact'
    MajorVersion = [uint64] 2
    MinorVersion = [byte] 0
    Active = $false
    PreservedSummaryMarker = [guid]::NewGuid().ToString('N')
}
$PascalEventLog = [pscustomobject] [ordered]@{
    Type = 'EFI_TCG2_EVENT_LOG_FORMAT_TCG_2'
    Data = ConvertTo-TestBase64 "PASCAL-$([guid]::NewGuid())"
    Truncated = $true
    Banks = @(
        [pscustomobject]@{
            Algorithm = 'SHA512'
            Pcrs = @{
                '0' = ConvertTo-TestBase64 "PCR-$([guid]::NewGuid())"
            }
        },
        [pscustomobject]@{
            Algorithm = 'SM3_256'
            Pcrs = [pscustomobject]@{}
        }
    )
    PreservedEventMarker = [guid]::NewGuid().ToString('N')
}
$PascalOutcome = Invoke-SeamValidation `
    -ListResponse @($PascalSummary) `
    -EventResponse $PascalEventLog
Assert-Equal $PascalOutcome.FailureMessage $null (
    'PascalCase SDK models are accepted'
)
Assert-Equal $PascalOutcome.ListCalls 1 'PascalCase list invocation count'
Assert-Equal $PascalOutcome.EventCalls 1 'PascalCase event invocation count'
Assert-True (
    [object]::ReferenceEquals(
        $PascalOutcome.Result.Summary,
        $PascalSummary
    )
) 'complete PascalCase summary object is preserved'
Assert-True (
    [object]::ReferenceEquals(
        $PascalOutcome.Result.EventLog,
        $PascalEventLog
    )
) 'complete PascalCase event-log object is preserved'
Assert-Equal $PascalOutcome.Result.Diagnosis 'TPM_INACTIVE' (
    'inactive evidence takes precedence after event retrieval'
)

$EmptyCollectionSummary = [pscustomobject]@{
    tpm = 'tpm-exact'
    major_version = [int64] 2
    minor_version = [int64] 0
    active = $true
}
$EmptyCollectionEvent = [pscustomobject]@{
    type = 'EFI_TCG2_EVENT_LOG_FORMAT_TCG_2'
    data = ''
    truncated = $false
    banks = @()
}
$EmptyCollectionOutcome = Invoke-SeamValidation `
    -ListResponse @($EmptyCollectionSummary) `
    -EventResponse $EmptyCollectionEvent
Assert-Equal $EmptyCollectionOutcome.FailureMessage $null (
    'non-null empty banks and canonical empty data are accepted'
)
Assert-Equal $EmptyCollectionOutcome.Result.Diagnosis 'NO_CAUSE_IDENTIFIED' (
    'empty valid evidence does not manufacture a cause'
)

$ValidDirectSummary = [pscustomobject]@{
    tpm = 'tpm-exact'
    major_version = [int64] 2
    minor_version = [int64] 0
    active = $true
}
$ValidDirectEvent = [pscustomobject]@{
    type = 'EFI_TCG2_EVENT_LOG_FORMAT_TCG_2'
    data = 'Zg=='
    truncated = $false
    banks = @(
        [pscustomobject]@{
            algorithm = 'SHA256'
            pcrs = [pscustomobject]@{ '0' = 'Zg==' }
        }
    )
}
$NullElementList = [Collections.Generic.List[object]]::new()
$NullElementList.Add($null)
$NullElementList.Add($ValidDirectSummary)
$InvalidListCases = @(
    [pscustomobject]@{
        Name = 'null list response'
        Response = $null
    },
    [pscustomobject]@{
        Name = 'null list element'
        Response = $NullElementList
    },
    [pscustomobject]@{
        Name = 'malformed nonmatching summary'
        Response = @(
            [pscustomobject]@{
                tpm = 'other'
                major_version = 2
                minor_version = 0
                active = 1
            },
            $ValidDirectSummary
        )
    },
    [pscustomobject]@{
        Name = 'Boolean major version'
        Response = @(
            [pscustomobject]@{
                tpm = 'tpm-exact'
                major_version = $true
                minor_version = 0
                active = $true
            }
        )
    },
    [pscustomobject]@{
        Name = 'Boolean minor version'
        Response = @(
            [pscustomobject]@{
                tpm = 'tpm-exact'
                major_version = 2
                minor_version = $false
                active = $true
            }
        )
    },
    [pscustomobject]@{
        Name = 'non-string TPM identifier'
        Response = @(
            [pscustomobject]@{
                tpm = 7
                major_version = 2
                minor_version = 0
                active = $true
            }
        )
    },
    [pscustomobject]@{
        Name = 'missing minor version'
        Response = @(
            [pscustomobject]@{
                tpm = 'tpm-exact'
                major_version = 2
                active = $true
            }
        )
    },
    [pscustomobject]@{
        Name = 'blank TPM identifier'
        Response = @(
            [pscustomobject]@{
                tpm = ' '
                major_version = 2
                minor_version = 0
                active = $true
            }
        )
    },
    [pscustomobject]@{
        Name = 'case-variant TPM only'
        Response = @(
            [pscustomobject]@{
                tpm = 'TPM-EXACT'
                major_version = 2
                minor_version = 0
                active = $true
            }
        )
    },
    [pscustomobject]@{
        Name = 'substring TPM only'
        Response = @(
            [pscustomobject]@{
                tpm = 'prefix-tpm-exact-suffix'
                major_version = 2
                minor_version = 0
                active = $true
            }
        )
    }
)
foreach ($InvalidListCase in $InvalidListCases) {
    $Outcome = Invoke-SeamValidation `
        -ListResponse $InvalidListCase.Response `
        -EventResponse $ValidDirectEvent
    Assert-True (
        -not [string]::IsNullOrWhiteSpace($Outcome.FailureMessage)
    ) "$($InvalidListCase.Name) is rejected"
    Assert-Equal $Outcome.ListCalls 1 (
        "$($InvalidListCase.Name) list invocation count"
    )
    Assert-Equal $Outcome.EventCalls 0 (
        "$($InvalidListCase.Name) blocks event retrieval"
    )
}

$NullBankCollection = [Collections.Generic.List[object]]::new()
$NullBankCollection.Add($null)
$TwoEventLogs = [Collections.Generic.List[object]]::new()
$TwoEventLogs.Add($ValidDirectEvent)
$TwoEventLogs.Add($ValidDirectEvent)
$InvalidEventCases = @(
    [pscustomobject]@{
        Name = 'null event response'
        Response = $null
    },
    [pscustomobject]@{
        Name = 'multiple event responses'
        Response = $TwoEventLogs
    },
    [pscustomobject]@{
        Name = 'missing event type'
        Response = [pscustomobject]@{
            data = 'Zg=='
            truncated = $false
            banks = @()
        }
    },
    [pscustomobject]@{
        Name = 'wrong event type'
        Response = [pscustomobject]@{
            type = 'OTHER'
            data = 'Zg=='
            truncated = $false
            banks = @()
        }
    },
    [pscustomobject]@{
        Name = 'noncanonical event data'
        Response = [pscustomobject]@{
            type = 'EFI_TCG2_EVENT_LOG_FORMAT_TCG_2'
            data = 'Zh=='
            truncated = $false
            banks = @()
        }
    },
    [pscustomobject]@{
        Name = 'non-string event data'
        Response = [pscustomobject]@{
            type = 'EFI_TCG2_EVENT_LOG_FORMAT_TCG_2'
            data = 7
            truncated = $false
            banks = @()
        }
    },
    [pscustomobject]@{
        Name = 'non-Boolean truncated value'
        Response = [pscustomobject]@{
            type = 'EFI_TCG2_EVENT_LOG_FORMAT_TCG_2'
            data = 'Zg=='
            truncated = 0
            banks = @()
        }
    },
    [pscustomobject]@{
        Name = 'null banks collection'
        Response = [pscustomobject]@{
            type = 'EFI_TCG2_EVENT_LOG_FORMAT_TCG_2'
            data = 'Zg=='
            truncated = $false
            banks = $null
        }
    },
    [pscustomobject]@{
        Name = 'non-collection banks object'
        Response = [pscustomobject]@{
            type = 'EFI_TCG2_EVENT_LOG_FORMAT_TCG_2'
            data = 'Zg=='
            truncated = $false
            banks = [pscustomobject]@{
                algorithm = 'SHA256'
                pcrs = [pscustomobject]@{}
            }
        }
    },
    [pscustomobject]@{
        Name = 'null bank element'
        Response = [pscustomobject]@{
            type = 'EFI_TCG2_EVENT_LOG_FORMAT_TCG_2'
            data = 'Zg=='
            truncated = $false
            banks = $NullBankCollection
        }
    },
    [pscustomobject]@{
        Name = 'unsupported bank algorithm'
        Response = [pscustomobject]@{
            type = 'EFI_TCG2_EVENT_LOG_FORMAT_TCG_2'
            data = 'Zg=='
            truncated = $false
            banks = @(
                [pscustomobject]@{
                    algorithm = 'sha256'
                    pcrs = [pscustomobject]@{}
                }
            )
        }
    },
    [pscustomobject]@{
        Name = 'null PCR map'
        Response = [pscustomobject]@{
            type = 'EFI_TCG2_EVENT_LOG_FORMAT_TCG_2'
            data = 'Zg=='
            truncated = $false
            banks = @(
                [pscustomobject]@{
                    algorithm = 'SHA256'
                    pcrs = $null
                }
            )
        }
    },
    [pscustomobject]@{
        Name = 'non-map PCR collection'
        Response = [pscustomobject]@{
            type = 'EFI_TCG2_EVENT_LOG_FORMAT_TCG_2'
            data = 'Zg=='
            truncated = $false
            banks = @(
                [pscustomobject]@{
                    algorithm = 'SHA256'
                    pcrs = @('Zg==')
                }
            )
        }
    },
    [pscustomobject]@{
        Name = 'noncanonical PCR digest'
        Response = [pscustomobject]@{
            type = 'EFI_TCG2_EVENT_LOG_FORMAT_TCG_2'
            data = 'Zg=='
            truncated = $false
            banks = @(
                [pscustomobject]@{
                    algorithm = 'SHA256'
                    pcrs = [pscustomobject]@{ '0' = 'Zh==' }
                }
            )
        }
    },
    [pscustomobject]@{
        Name = 'non-string PCR digest'
        Response = [pscustomobject]@{
            type = 'EFI_TCG2_EVENT_LOG_FORMAT_TCG_2'
            data = 'Zg=='
            truncated = $false
            banks = @(
                [pscustomobject]@{
                    algorithm = 'SHA256'
                    pcrs = [pscustomobject]@{ '0' = 7 }
                }
            )
        }
    }
)
foreach ($InvalidEventCase in $InvalidEventCases) {
    $Outcome = Invoke-SeamValidation `
        -ListResponse @($ValidDirectSummary) `
        -EventResponse $InvalidEventCase.Response
    Assert-True (
        -not [string]::IsNullOrWhiteSpace($Outcome.FailureMessage)
    ) "$($InvalidEventCase.Name) is rejected"
    Assert-Equal $Outcome.ListCalls 1 (
        "$($InvalidEventCase.Name) list invocation count"
    )
    Assert-Equal $Outcome.EventCalls 1 (
        "$($InvalidEventCase.Name) event invocation count"
    )
}

$SanitizationSecret = "secret-$([guid]::NewGuid().ToString('N'))"
$ListFailureOutcome = Invoke-SeamValidation `
    -ListResponse $null `
    -EventResponse $null `
    -ThrowList $true `
    -TransportSecret $SanitizationSecret
Assert-True (
    $ListFailureOutcome.FailureMessage.Contains($ListOperation)
) 'sanitized list failure names its operationId'
Assert-True (
    -not $ListFailureOutcome.FailureRendering.Contains($SanitizationSecret)
) 'sanitized list failure omits transport and server secrets'
foreach ($ForbiddenFailureText in @(
    'nested transport message',
    'response body'
)) {
    Assert-True (
        -not $ListFailureOutcome.FailureRendering.Contains(
            $ForbiddenFailureText
        )
    ) "sanitized list failure omits $ForbiddenFailureText"
}
Assert-Equal $ListFailureOutcome.ListCalls 1 (
    'throwing list invoker call count'
)
Assert-Equal $ListFailureOutcome.EventCalls 0 (
    'throwing list invoker blocks event retrieval'
)

$EventFailureOutcome = Invoke-SeamValidation `
    -ListResponse @($ValidDirectSummary) `
    -EventResponse $null `
    -ThrowEvent $true `
    -TransportSecret $SanitizationSecret
Assert-True (
    $EventFailureOutcome.FailureMessage.Contains($EventOperation)
) 'sanitized event failure names its operationId'
Assert-True (
    -not $EventFailureOutcome.FailureRendering.Contains($SanitizationSecret)
) 'sanitized event failure omits transport and server secrets'
foreach ($ForbiddenFailureText in @(
    'nested transport message',
    'response body'
)) {
    Assert-True (
        -not $EventFailureOutcome.FailureRendering.Contains(
            $ForbiddenFailureText
        )
    ) "sanitized event failure omits $ForbiddenFailureText"
}
Assert-Equal $EventFailureOutcome.ListCalls 1 (
    'throwing event invoker list call count'
)
Assert-Equal $EventFailureOutcome.EventCalls 1 (
    'throwing event invoker event call count'
)

$RunId = [guid]::NewGuid().ToString('N')
$SessionToken = "session-$([guid]::NewGuid().ToString('N'))"
$Cases = @(
    [pscustomobject] [ordered]@{
        HostId = "host/$RunId west"
        TpmId = "tpm/$RunId`?slot=A"
        Active = $true
        Truncated = $true
        Diagnosis = 'EVENT_LOG_TRUNCATED'
    },
    [pscustomobject] [ordered]@{
        HostId = "host/$RunId inactive"
        TpmId = "tpm/$RunId`#slot=B"
        Active = $false
        Truncated = $false
        Diagnosis = 'TPM_INACTIVE'
    },
    [pscustomobject] [ordered]@{
        HostId = "host/$RunId control"
        TpmId = "tpm/$RunId+slot=C"
        Active = $true
        Truncated = $false
        Diagnosis = 'NO_CAUSE_IDENTIFIED'
    }
)

$ListResponses = [Collections.ArrayList]::new()
$EventResponses = [Collections.ArrayList]::new()
$ExpectedSummaries = [Collections.ArrayList]::new()
$ExpectedEventLogs = [Collections.ArrayList]::new()
foreach ($Case in $Cases) {
    $Summary = [pscustomobject] [ordered]@{
        tpm = $Case.TpmId
        major_version = 2
        minor_version = 0
        active = $Case.Active
        runtime_marker = [guid]::NewGuid().ToString('N')
    }
    $Distractor = [pscustomobject] [ordered]@{
        tpm = $Case.TpmId.ToUpperInvariant()
        major_version = 2
        minor_version = 0
        active = (-not $Case.Active)
        runtime_marker = [guid]::NewGuid().ToString('N')
    }
    $EventLog = [pscustomobject] [ordered]@{
        type = 'EFI_TCG2_EVENT_LOG_FORMAT_TCG_2'
        data = ConvertTo-TestBase64 "TCG-EVENT-$([guid]::NewGuid())"
        truncated = $Case.Truncated
        banks = @(
            [pscustomobject] [ordered]@{
                algorithm = 'SHA384'
                pcrs = [pscustomobject] [ordered]@{
                    '7' = ConvertTo-TestBase64 "PCR7-$([guid]::NewGuid())"
                    '11' = ConvertTo-TestBase64 "PCR11-$([guid]::NewGuid())"
                }
            },
            [pscustomobject] [ordered]@{
                algorithm = 'SHA256'
                pcrs = [pscustomobject] [ordered]@{
                    '0' = ConvertTo-TestBase64 "PCR0-$([guid]::NewGuid())"
                }
            }
        )
        runtime_marker = [guid]::NewGuid().ToString('N')
    }
    [void] $ListResponses.Add([object[]] @($Distractor, $Summary))
    [void] $EventResponses.Add($EventLog)
    [void] $ExpectedSummaries.Add($Summary)
    [void] $ExpectedEventLogs.Add($EventLog)
}

$DuplicateHost = "host/$RunId duplicate"
$DuplicateTpm = "tpm/$RunId duplicate"
$DuplicateSummary = [pscustomobject] [ordered]@{
    tpm = $DuplicateTpm
    major_version = 2
    minor_version = 0
    active = $true
}
[void] $ListResponses.Add(
    [object[]] @($DuplicateSummary, $DuplicateSummary)
)

$ResponseMap = [ordered]@{}
$ResponseMap[$ListOperation] = $ListResponses
$ResponseMap[$EventOperation] = $EventResponses
$Config = [ordered]@{ responses = $ResponseMap }

$TempRoot = Join-Path (
    [IO.Path]::GetTempPath()
) "vcf91-0096-$([guid]::NewGuid().ToString('N'))"
[void] (New-Item -ItemType Directory -Path $TempRoot)
$ConfigPath = Join-Path $TempRoot 'config.json'
$LogPath = Join-Path $TempRoot 'requests.jsonl'
$ReadyPath = Join-Path $TempRoot 'ready.json'
[IO.File]::WriteAllText(
    $ConfigPath,
    ($Config | ConvertTo-Json -Depth 40 -Compress),
    [Text.UTF8Encoding]::new($false)
)

$StartInfo = [Diagnostics.ProcessStartInfo]::new()
$StartInfo.FileName = 'python3'
$StartInfo.UseShellExecute = $false
$StartInfo.RedirectStandardOutput = $true
$StartInfo.RedirectStandardError = $true
foreach ($Argument in @(
    '-B',
    $MockPath,
    '--contract', $ContractPath,
    '--config', $ConfigPath,
    '--log', $LogPath,
    '--ready-file', $ReadyPath
)) {
    [void] $StartInfo.ArgumentList.Add($Argument)
}

$MockProcess = [Diagnostics.Process]::new()
$MockProcess.StartInfo = $StartInfo
$HttpClient = $null
try {
    Assert-True $MockProcess.Start() 'mock process started'
    $Deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path -LiteralPath $ReadyPath -PathType Leaf)) {
        if ($MockProcess.HasExited) {
            $MockError = $MockProcess.StandardError.ReadToEnd()
            throw "mock exited during startup: $MockError"
        }
        if ([DateTime]::UtcNow -ge $Deadline) {
            throw 'mock did not become ready'
        }
        Start-Sleep -Milliseconds 25
    }

    $Ready = Get-Content -Raw -LiteralPath $ReadyPath | ConvertFrom-Json
    Assert-Equal $Ready.host '127.0.0.1' 'mock bind host'
    Assert-Equal @($Ready.operation_ids).Count 2 'mock route count'
    Assert-Equal (
        @($Ready.operation_ids | Sort-Object) -join ','
    ) (
        @($ExpectedOperationIds | Sort-Object) -join ','
    ) 'mock route operationIds'
    $Origin = "http://127.0.0.1:$($Ready.port)"

    $Handler = [Net.Http.HttpClientHandler]::new()
    $Handler.AllowAutoRedirect = $false
    $Handler.UseProxy = $false
    $HttpClient = [Net.Http.HttpClient]::new($Handler, $true)

    $ServerHandle = [pscustomobject]@{
        Origin = $Origin
        RuntimeHandle = [guid]::NewGuid().ToString('N')
    }
    $FixtureState = [pscustomobject]@{
        Invocations = [Collections.Generic.List[object]]::new()
    }
    $OperationInvoker = {
        param(
            [string] $OperationId,
            [hashtable] $Parameters,
            $SdkServer
        )

        if ($OperationId -cnotin @($ListOperation, $EventOperation)) {
            throw "operation outside contract: $OperationId"
        }
        if ($null -eq $Parameters) {
            throw 'operation parameter map is null'
        }
        if (-not [object]::ReferenceEquals($SdkServer, $ServerHandle)) {
            throw 'the caller-owned server handle was not preserved'
        }

        $ExpectedKeys = if ($OperationId -ceq $ListOperation) {
            @('Host')
        }
        else {
            @('Host', 'Tpm')
        }
        Assert-Equal (
            @($Parameters.Keys | Sort-Object) -join ','
        ) (
            @($ExpectedKeys | Sort-Object) -join ','
        ) "$OperationId parameter key set"
        foreach ($Unset in @(
            'Filter',
            'Active',
            'MajorVersions',
            'MajorVersion',
            'Body',
            'RequestBody'
        )) {
            if ($Parameters.ContainsKey($Unset)) {
                throw "$OperationId supplied unset optional parameter $Unset"
            }
        }

        $EscapedHost = [uri]::EscapeDataString([string] $Parameters.Host)
        if ($OperationId -ceq $ListOperation) {
            $Target = (
                '/api/vcenter/trusted-infrastructure/hosts/{0}/hardware/tpm' -f
                $EscapedHost
            )
        }
        else {
            $EscapedTpm = [uri]::EscapeDataString([string] $Parameters.Tpm)
            $Target = (
                '/api/vcenter/trusted-infrastructure/hosts/{0}/hardware/' +
                'tpm/{1}/event-log'
            ) -f $EscapedHost, $EscapedTpm
        }
        $FixtureState.Invocations.Add(
            [pscustomobject]@{
                OperationId = $OperationId
                Parameters = $Parameters.Clone()
                Server = $SdkServer
                Target = $Target
            }
        )

        $Request = [Net.Http.HttpRequestMessage]::new(
            [Net.Http.HttpMethod]::Get,
            [uri] ($Origin + $Target)
        )
        $Response = $null
        try {
            [void] $Request.Headers.TryAddWithoutValidation(
                'vmware-api-session-id',
                $SessionToken
            )
            $Request.Headers.Accept.Add(
                [Net.Http.Headers.MediaTypeWithQualityHeaderValue]::new(
                    'application/json'
                )
            )
            $Response = $HttpClient.SendAsync(
                $Request
            ).GetAwaiter().GetResult()
            if ([int] $Response.StatusCode -ne 200) {
                throw "$OperationId fixture returned HTTP $([int] $Response.StatusCode)"
            }
            $Body = $Response.Content.ReadAsStringAsync(
            ).GetAwaiter().GetResult()
            $Decoded = ConvertFrom-Json `
                -InputObject $Body `
                -Depth 100 `
                -NoEnumerate
            Write-Output -NoEnumerate $Decoded
        }
        finally {
            if ($null -ne $Response) {
                $Response.Dispose()
            }
            $Request.Dispose()
        }
    }.GetNewClosure()

    $Client = New-VcfVcenterTpmEvidenceClient `
        -Server $ServerHandle `
        -OperationInvoker $OperationInvoker
    Assert-Equal $FixtureState.Invocations.Count 0 (
        'client construction performs no request'
    )

    $Results = [Collections.ArrayList]::new()
    foreach ($Case in $Cases) {
        $Result = Get-VcfHostTpmFailureEvidence `
            -Client $Client `
            -HostId $Case.HostId `
            -TpmId $Case.TpmId
        [void] $Results.Add($Result)
    }
    Assert-Equal $Results.Count $Cases.Count 'result count'
    for ($Index = 0; $Index -lt $Cases.Count; $Index++) {
        $Result = $Results[$Index]
        $Case = $Cases[$Index]
        Assert-Equal (
            @($Result.PSObject.Properties.Name) -join ','
        ) 'HostId,TpmId,Summary,EventLog,Diagnosis' (
            'result property order'
        )
        Assert-Equal $Result.HostId $Case.HostId 'result host identifier'
        Assert-Equal $Result.TpmId $Case.TpmId 'result TPM identifier'
        Assert-JsonEqual $Result.Summary $ExpectedSummaries[$Index] (
            'matching summary preservation'
        )
        Assert-JsonEqual $Result.EventLog $ExpectedEventLogs[$Index] (
            'event-log preservation'
        )
        Assert-Equal $Result.Diagnosis $Case.Diagnosis (
            'evidence-only diagnosis'
        )
    }

    $DuplicateFailed = $false
    try {
        [void] (Get-VcfHostTpmFailureEvidence `
            -Client $Client `
            -HostId $DuplicateHost `
            -TpmId $DuplicateTpm)
    }
    catch {
        $DuplicateFailed = $true
    }
    Assert-True $DuplicateFailed 'duplicate exact TPM is rejected'
    Assert-Equal $FixtureState.Invocations.Count 7 (
        'duplicate match does not start event-log retrieval'
    )

    foreach ($BlankCase in @(
        @{ HostId = ' '; TpmId = $Cases[0].TpmId },
        @{ HostId = $Cases[0].HostId; TpmId = "`t" }
    )) {
        $BlankFailed = $false
        try {
            [void] (Get-VcfHostTpmFailureEvidence `
                -Client $Client `
                -HostId $BlankCase.HostId `
                -TpmId $BlankCase.TpmId)
        }
        catch {
            $BlankFailed = $true
        }
        Assert-True $BlankFailed 'blank identifier is rejected'
    }
    Assert-Equal $FixtureState.Invocations.Count 7 (
        'blank identifiers are rejected before dispatch'
    )

    $Requests = @(
        Get-Content -LiteralPath $LogPath |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
    Assert-Equal $Requests.Count 7 'wire request count'

    $ExpectedWire = [Collections.Generic.List[object]]::new()
    foreach ($Case in $Cases) {
        $EscapedHost = [uri]::EscapeDataString($Case.HostId)
        $EscapedTpm = [uri]::EscapeDataString($Case.TpmId)
        $ExpectedWire.Add(
            [pscustomobject]@{
                OperationId = $ListOperation
                Target = (
                    '/api/vcenter/trusted-infrastructure/hosts/{0}/hardware/tpm' -f
                    $EscapedHost
                )
            }
        )
        $ExpectedWire.Add(
            [pscustomobject]@{
                OperationId = $EventOperation
                Target = (
                    (
                        '/api/vcenter/trusted-infrastructure/hosts/{0}/hardware/' +
                        'tpm/{1}/event-log'
                    ) -f @($EscapedHost, $EscapedTpm)
                )
            }
        )
    }
    $ExpectedWire.Add(
        [pscustomobject]@{
            OperationId = $ListOperation
            Target = (
                '/api/vcenter/trusted-infrastructure/hosts/{0}/hardware/tpm' -f
                [uri]::EscapeDataString($DuplicateHost)
            )
        }
    )

    for ($Index = 0; $Index -lt $Requests.Count; $Index++) {
        $Request = $Requests[$Index]
        $Expected = $ExpectedWire[$Index]
        Assert-Equal $Request.sequence ($Index + 1) 'request sequence'
        Assert-Equal $Request.operation_id $Expected.OperationId (
            'request operationId'
        )
        Assert-Equal $Request.method 'GET' 'request method'
        Assert-Equal $Request.target $Expected.Target 'exact request target'
        Assert-Equal $Request.path $Expected.Target 'exact request path'
        Assert-Equal $Request.query '' 'absent query string'
        Assert-True (-not ([string] $Request.target).Contains('?')) (
            'request has no trailing question mark or optional query'
        )
        Assert-Equal $Request.body_length 0 'zero-byte request body'
        Assert-Equal $Request.body_base64 '' 'empty request body encoding'

        $SessionValues = @(
            Get-RequestHeaderValues $Request 'vmware-api-session-id'
        )
        Assert-Equal $SessionValues.Count 1 (
            'single vCenter session header'
        )
        Assert-Equal $SessionValues[0] $SessionToken (
            'vCenter session header value'
        )
        $AcceptValues = @(
            Get-RequestHeaderValues $Request 'Accept'
        )
        Assert-Equal $AcceptValues.Count 1 'single Accept header'
        Assert-Equal $AcceptValues[0] 'application/json' (
            'JSON Accept header'
        )
        foreach ($ForbiddenHeader in @(
            'Authorization',
            'Content-Type',
            'Content-Length',
            'Transfer-Encoding'
        )) {
            Assert-Equal @(
                Get-RequestHeaderValues $Request $ForbiddenHeader
            ).Count 0 "absent $ForbiddenHeader header"
        }
    }

    Write-Output 'ALL TESTS PASSED'
}
finally {
    if ($null -ne $HttpClient) {
        $HttpClient.Dispose()
    }
    if ($null -ne $MockProcess) {
        if (-not $MockProcess.HasExited) {
            $MockProcess.Kill($true)
        }
        $MockProcess.WaitForExit()
        $MockProcess.Dispose()
    }
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
    Remove-Module VcfVcenterTpmEvidence -Force -ErrorAction SilentlyContinue
}
