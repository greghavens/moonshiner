#Requires -Version 7.2

Set-StrictMode -Version Latest

<#
.SYNOPSIS
    Retrieves the complete VCF Operations alert collection in a stable order.

.DESCRIPTION
    Opens a VCF Operations session, walks the paginated getAlerts collection to
    completion, and emits one object per alert in a deterministic order.

    See README.md for the required behaviour and docs/contract.json for the wire
    contract this function must honour.

.PARAMETER Server
    Host name or address of the VCF Operations appliance.

.PARAMETER Credential
    Credential used to acquire the session token.

.PARAMETER Port
    TCP port of the appliance. Defaults to the protocol default.

.PARAMETER Protocol
    'https' (default) or 'http'.

.PARAMETER AuthSource
    Name of the VCF Operations authentication source. Omitted for local accounts.

.PARAMETER ResourceId
    Restricts the inventory to alerts raised by these resource identifiers.
    When omitted, every alert is returned.

.PARAMETER PageSize
    Number of records requested per page. Defaults to 100.

.PARAMETER SkipCertificateCheck
    Accepts an untrusted server certificate.
#>
function Get-VcfOpsAlertInventory {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Server,

        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [System.Management.Automation.PSCredential] $Credential,

        [ValidateRange(1, 65535)]
        [int] $Port,

        [ValidateSet('http', 'https')]
        [string] $Protocol = 'https',

        [ValidateNotNullOrEmpty()]
        [string] $AuthSource,

        [string[]] $ResourceId,

        [ValidateRange(1, 1000)]
        [int] $PageSize = 100,

        [switch] $SkipCertificateCheck
    )

    throw [System.NotImplementedException]::new(
        'Get-VcfOpsAlertInventory is not implemented yet.')
}

Export-ModuleMember -Function 'Get-VcfOpsAlertInventory'
