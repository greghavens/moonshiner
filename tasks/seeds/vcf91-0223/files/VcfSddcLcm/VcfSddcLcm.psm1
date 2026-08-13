Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

<#
.SYNOPSIS
    Retrieve every node of a VCF 9.1 SDDC LCM component in a stable order.

.DESCRIPTION
    Wraps the SDDC LCM operations pinned in docs/contract.json:

      getComponents      GET /v1/components
      getComponentNodes  GET /v1/components/{componentId}/nodes

    The connection produced by Connect-VcfInstallerServer (VMware.Sdk.Vcf.Installer)
    supplies the bearer token used for both operations.

.PARAMETER Server
    A connected VCF server object, as returned by Connect-VcfInstallerServer. Its
    SessionSecret is sent as 'Authorization: Bearer <SessionSecret>'.

.PARAMETER ServiceUri
    Base URI of the SDDC LCM service, for example https://vcf.example.com/sddc-lcm.
    Operation paths from the contract are appended to it.

.PARAMETER ComponentId
    Identifier of the component whose nodes are retrieved. getComponents is not
    called in this parameter set.

.PARAMETER ComponentType
    Resolve the component by its componentType through getComponents. Exactly one
    component must match, otherwise the command fails.

.PARAMETER Scope
    Optional getComponents 'scope' filter, FLEET or INSTANCE. When omitted, the
    scope query parameter is not sent at all.

.PARAMETER NodeType
    Optional node type filter. Sent as the single 'nodeTypes' query parameter whose
    value is the supplied types joined by a literal comma. When omitted, the
    nodeTypes query parameter is not sent at all.

.PARAMETER PageSize
    Optional page size, 1 through 50. When omitted, the pageSize query parameter is
    not sent at all and the service default applies.

.OUTPUTS
    PSCustomObject, one per node.
#>
function Get-VcfSddcLcmComponentNode {
    [CmdletBinding(DefaultParameterSetName = 'ById')]
    [OutputType([PSCustomObject])]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [object] $Server,

        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [uri] $ServiceUri,

        [Parameter(Mandatory, ParameterSetName = 'ById')]
        [ValidateNotNullOrEmpty()]
        [string] $ComponentId,

        [Parameter(Mandatory, ParameterSetName = 'ByType')]
        [ValidateNotNullOrEmpty()]
        [string] $ComponentType,

        [Parameter(ParameterSetName = 'ByType')]
        [ValidateSet('FLEET', 'INSTANCE')]
        [string] $Scope,

        [Parameter()]
        [ValidateNotNullOrEmpty()]
        [string[]] $NodeType,

        [Parameter()]
        [ValidateRange(1, 50)]
        [int] $PageSize
    )

    throw [System.NotImplementedException]::new(
        'TODO: implement getComponents resolution and getComponentNodes pagination')
}

Export-ModuleMember -Function 'Get-VcfSddcLcmComponentNode'
