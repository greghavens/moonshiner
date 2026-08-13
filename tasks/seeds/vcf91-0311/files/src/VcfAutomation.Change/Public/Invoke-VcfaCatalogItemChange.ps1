<#
.SYNOPSIS
    Requests a catalog item into a project and then submits a day-2 action against one of
    the resources it created, reporting the outcome of every step.

.DESCRIPTION
    Five steps, in this order, using only the operations in docs/contract.json:

      1. ResolveProject        GET  /project-service/api/projects
                               No documented name filter exists, so page the collection and
                               match ProjectName exactly, client-side.
      2. ResolveCatalogItem    GET  /catalog/api/items
                               search is a substring match, so confirm the exact name
                               client-side. Scope the query to the project found in step 1.
      3. RequestCatalogItem    POST /catalog/api/items/{id}/request
                               Body from Initialize-VcfaCatalogItemRequest. The response is
                               an array whose entries carry only deploymentId and
                               deploymentName - this is the ONLY place the deployment id is
                               returned, so capture it here.
      4. ResolveResource       GET  /deployment/api/deployments/{deploymentId}/resources
                               names is an exact-name filter; use it for ResourceName.
      5. SubmitResourceAction  POST /deployment/api/deployments/{deploymentId}/resources/{resourceId}/requests
                               Body from Initialize-VcfaResourceActionRequest.

    Reporting rules - these matter as much as the calls:

      * NEVER throw because a step failed. A step failure is a result, not an exception, and
        it is returned. The HTTP layer already declines to throw on error statuses.
      * Stop at the first failed step. Every later step is recorded with Status 'Skipped'.
      * Report what actually happened before the failure. If step 3 succeeded then a
        deployment exists on the appliance, and DeploymentId/DeploymentName must be populated
        on the returned object even when a later step fails - that identifier is the only way
        an operator can find or retry the change.
      * Do not attempt to undo earlier steps. Deleting or patching a deployment is out of
        contract; see outOfScope in docs/contract.json.
      * A failed step's Detail must name the HTTP status code, and must include the server's
        message when the response carried one (Get-VcfaErrorMessage returns it, or $null).
        A step that failed client-side, such as a name that matched nothing, must say what
        was being looked for.

.OUTPUTS
    A single PSCustomObject:

        Status         'Succeeded' when all five steps succeeded, otherwise 'Failed'
        FailedStep     name of the first failed step, or $null
        ProjectId      populated once step 1 succeeds, else $null
        CatalogItemId  populated once step 2 succeeds, else $null
        DeploymentId   populated once step 3 succeeds, else $null
        DeploymentName populated once step 3 succeeds, else $null
        ResourceId     populated once step 4 succeeds, else $null
        Steps          five entries, in the order above, each with:
                           Name    one of ResolveProject, ResolveCatalogItem,
                                   RequestCatalogItem, ResolveResource, SubmitResourceAction
                           Status  'Succeeded' | 'Failed' | 'Skipped'
                           Detail  non-empty string for every step, including skipped ones

.EXAMPLE
    $session = Connect-VcfaOrgSession -BaseUri 'https://vcfa.rainpole.io' `
                                      -SddcManagerServer 'sddc.rainpole.io' -Credential $cred
    $report = Invoke-VcfaCatalogItemChange -Session $session `
                  -ProjectName 'eng-platform' -CatalogItemName 'Ubuntu 24.04 Small' `
                  -DeploymentName 'billing-db-02' -Inputs @{ size = 'small' } `
                  -ResourceName 'db-node' -ActionId 'Cloud.vSphere.Machine.Resize' `
                  -ActionInputs @{ cpuCount = 4 }
    $report.Steps | Format-Table
#>
function Invoke-VcfaCatalogItemChange {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [object] $Session,

        [Parameter(Mandatory)]
        [string] $ProjectName,

        [Parameter(Mandatory)]
        [string] $CatalogItemName,

        [Parameter(Mandatory)]
        [string] $DeploymentName,

        [Parameter(Mandatory)]
        [System.Collections.IDictionary] $Inputs,

        [Parameter(Mandatory)]
        [string] $ResourceName,

        [Parameter(Mandatory)]
        [string] $ActionId,

        # Optional. Anything the caller leaves unbound must not reach the wire as an empty
        # value - see clientRules.omitUnsetOptionalFields in docs/contract.json.
        [System.Collections.IDictionary] $ActionInputs,

        [string] $Reason,

        [string] $CatalogItemVersion,

        [int] $BulkRequestCount,

        [string] $ActionReason
    )

    throw [System.NotImplementedException]::new(
        'Invoke-VcfaCatalogItemChange is not implemented yet.')
}
