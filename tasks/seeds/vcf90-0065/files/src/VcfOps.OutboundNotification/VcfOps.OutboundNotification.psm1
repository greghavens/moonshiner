Set-StrictMode -Version Latest

function New-VcfOpsOutboundNotification {
    <#
    .SYNOPSIS
        Onboard an outbound notification on VCF Operations 9.0 as an ordered,
        three step change and report the outcome of every step.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [object] $Server,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $PluginName,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $PluginTypeId,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $RuleName,

        [ValidateNotNullOrEmpty()]
        [string] $Description,

        [ValidateNotNull()]
        [hashtable] $ConfigValues,

        [ValidateNotNullOrEmpty()]
        [string] $TemplateId,

        [ValidateNotNullOrEmpty()]
        [string[]] $Criticalities
    )

    throw 'New-VcfOpsOutboundNotification has not been implemented.'
}

Export-ModuleMember -Function New-VcfOpsOutboundNotification
