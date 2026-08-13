Set-StrictMode -Version Latest

<#
.SYNOPSIS
    Gates a VCF Automation day-2 resource action behind an availability precheck.

.DESCRIPTION
    VCF Automation 9.1 has no published API specification and no module in the
    VMware.Sdk.Vcf PowerCLI family, so this module speaks the deployment REST API
    directly. The wire contract it must honour is docs/contract.json, which was
    transcribed from the xAPIs reference pages listed in docs/official_sources.json.
#>

function Connect-VcfAutomationServer {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string]$Server,

        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [securestring]$AccessToken,

        [ValidateRange(1, 65535)]
        [int]$Port = 443,

        [ValidateSet('http', 'https')]
        [string]$Protocol = 'https'
    )

    throw 'Not implemented.'
}

function Invoke-VcfAutomationResourceAction {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [object]$Connection,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string]$DeploymentId,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string]$ResourceName,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string]$ActionId,

        [ValidateNotNullOrEmpty()]
        [string]$Reason,

        [AllowNull()]
        [hashtable]$Inputs
    )

    throw 'Not implemented.'
}

Export-ModuleMember -Function Connect-VcfAutomationServer, Invoke-VcfAutomationResourceAction
