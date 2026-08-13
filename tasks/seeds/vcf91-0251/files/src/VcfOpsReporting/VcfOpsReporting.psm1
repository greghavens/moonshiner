#Requires -Version 7.0
Set-StrictMode -Version Latest

<#
    VcfOpsReporting -- on-demand report generation against the VCF Operations API.

    The wire contract these functions must honour is docs/contract.json, which is derived from
    the VCF 9.1 vcf-operations OpenAPI document. Read it before changing anything here.

    All HTTP work is built on the VMware.Sdk.Vcf.Ops PowerCLI module (installed by the
    environment). Nothing in this repository vendors or re-implements that SDK.

    NOT IMPLEMENTED. Every public function below throws.
#>

$script:BasePath = '/suite-api'

function Connect-VcfOpsReportingSession {
    <#
    .SYNOPSIS
        Establish an authenticated session against a VCF Operations instance.
    .DESCRIPTION
        Wraps Connect-VcfOpsServer. The session object returned by this function is what every
        other function in this module takes as -Session.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Server,
        [int] $Port,
        [ValidateSet('http', 'https')] [string] $Protocol = 'https',
        [Parameter(Mandatory)] [pscredential] $Credential,
        [string] $AuthSource,
        [switch] $SkipCertificateCheck
    )
    throw [System.NotImplementedException]::new('Connect-VcfOpsReportingSession is not implemented.')
}

function Start-VcfOpsReportGeneration {
    <#
    .SYNOPSIS
        Request generation of a report. Returns as soon as the request is accepted.
    .DESCRIPTION
        Report generation is asynchronous: this returns a report whose status is not yet terminal.
        Feed the returned Id to Wait-VcfOpsReportGeneration.

        Only the parameters the caller actually supplies may appear in the request body.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Session,
        [Parameter(Mandatory)] [guid] $ReportDefinitionId,
        [Parameter(Mandatory)] [guid] $ResourceId,
        [string] $Name,
        [string] $Description,
        [string[]] $Subject,
        [string] $TraversalSpecName,
        [switch] $Publish
    )
    throw [System.NotImplementedException]::new('Start-VcfOpsReportGeneration is not implemented.')
}

function Wait-VcfOpsReportGeneration {
    <#
    .SYNOPSIS
        Poll a report until its status reaches a terminal state.
    .DESCRIPTION
        Returns the final report on terminal success. Throws on terminal failure and on timeout.
        The terminal/non-terminal partition is recorded in docs/contract.json under
        taskDefined.reportStatus.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Session,
        [Parameter(Mandatory)] [guid] $ReportId,
        [double] $PollIntervalSeconds = 5,
        [double] $TimeoutSeconds = 900
    )
    throw [System.NotImplementedException]::new('Wait-VcfOpsReportGeneration is not implemented.')
}

function Save-VcfOpsReport {
    <#
    .SYNOPSIS
        Download a generated report to disk.
    .DESCRIPTION
        -Format is optional. When the caller omits it the request must not carry a format query
        parameter at all.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Session,
        [Parameter(Mandatory)] [guid] $ReportId,
        [Parameter(Mandatory)] [string] $Path,
        [ValidateSet('CSV', 'PDF')] [string] $Format
    )
    throw [System.NotImplementedException]::new('Save-VcfOpsReport is not implemented.')
}

function Invoke-VcfOpsReportRun {
    <#
    .SYNOPSIS
        Generate a report, wait for it to finish, and download it.
    .DESCRIPTION
        The end-to-end asynchronous flow: start, poll to a terminal state, then download.
        Nothing is downloaded if generation does not reach terminal success.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Session,
        [Parameter(Mandatory)] [guid] $ReportDefinitionId,
        [Parameter(Mandatory)] [guid] $ResourceId,
        [Parameter(Mandatory)] [string] $Path,
        [ValidateSet('CSV', 'PDF')] [string] $Format,
        [string] $Name,
        [string] $Description,
        [string[]] $Subject,
        [string] $TraversalSpecName,
        [switch] $Publish,
        [double] $PollIntervalSeconds = 5,
        [double] $TimeoutSeconds = 900
    )
    throw [System.NotImplementedException]::new('Invoke-VcfOpsReportRun is not implemented.')
}

Export-ModuleMember -Function @(
    'Connect-VcfOpsReportingSession',
    'Start-VcfOpsReportGeneration',
    'Wait-VcfOpsReportGeneration',
    'Save-VcfOpsReport',
    'Invoke-VcfOpsReportRun'
)
