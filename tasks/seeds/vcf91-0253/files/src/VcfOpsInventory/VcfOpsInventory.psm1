Set-StrictMode -Version Latest

function Get-VcfOpsResourceInventory {
    <#
    .SYNOPSIS
        Takes a complete, stably ordered snapshot of the VCF Operations resource
        inventory.

    .DESCRIPTION
        Enumerates the getResources collection through the VMware.Sdk.Vcf.Ops
        PowerCLI module and returns every matching resource exactly once, in an
        order that does not depend on how the server happened to page them or on
        the locale of the host the snapshot ran on.

        See docs/contract.json for the pagination and ordering rules, and README.md
        for the shape of the returned object.

    .PARAMETER Server
        A connection handle from Connect-VcfOpsServer.

    .PARAMETER PageSize
        Resources requested per page.

    .PARAMETER Name
        Restrict the snapshot to these resource names.

    .PARAMETER AdapterKind
        Restrict the snapshot to these adapter kind keys.

    .PARAMETER ResourceKind
        Restrict the snapshot to these resource kind keys.

    .PARAMETER ResourceHealth
        Restrict the snapshot to these health values.

    .PARAMETER CreatedAfter
        Restrict the snapshot to resources created after this many seconds since
        1970-01-01T00:00:00Z.

    .PARAMETER PropertyName
        Restrict the snapshot to resources carrying this property.

    .PARAMETER PropertyValue
        Restrict the snapshot to resources whose PropertyName has this value.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Server,
        [int]      $PageSize = 1000,
        [string[]] $Name,
        [string[]] $AdapterKind,
        [string[]] $ResourceKind,
        [string[]] $ResourceHealth,
        [long]     $CreatedAfter,
        [string]   $PropertyName,
        [string]   $PropertyValue
    )

    throw [System.NotImplementedException]::new(
        'Get-VcfOpsResourceInventory is not implemented yet.')
}

Export-ModuleMember -Function 'Get-VcfOpsResourceInventory'
