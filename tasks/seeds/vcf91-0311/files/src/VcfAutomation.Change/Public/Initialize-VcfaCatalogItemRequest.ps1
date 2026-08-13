<#
.SYNOPSIS
    Builds the CatalogItemRequest body for POST /catalog/api/items/{id}/request.

.DESCRIPTION
    Client-side only; sends nothing. Named after the Initialize-* convention the generated
    VMware.Sdk.Vcf.* modules use for body builders, and it must behave like them: a
    parameter the caller did not bind does not appear in the returned body at all.

    Field names, types and limits come from docs/contract.json ->
    operations.requestCatalogItemInstances.requestBody.

.OUTPUTS
    System.Collections.Specialized.OrderedDictionary - passed straight to the HTTP layer and
    serialised verbatim, so every key present here becomes a key on the wire.
#>
function Initialize-VcfaCatalogItemRequest {
    [CmdletBinding()]
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [string] $DeploymentName,

        [string] $ProjectId,

        [System.Collections.IDictionary] $Inputs,

        [string] $Version,

        [string] $Reason,

        [int] $BulkRequestCount
    )

    throw [System.NotImplementedException]::new(
        'Initialize-VcfaCatalogItemRequest is not implemented yet.')
}
