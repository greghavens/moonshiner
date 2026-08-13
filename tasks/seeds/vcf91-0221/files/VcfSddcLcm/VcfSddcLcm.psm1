Set-StrictMode -Version Latest

function Set-VcfSddcLcmConfiguration {
    <#
    .SYNOPSIS
    Connects VCF SDDC LCM to SDDC Manager and Fleet LCM and waits for completion.

    .DESCRIPTION
    Implements the setConfig and getTask operations recorded in
    docs/contract.json. VMware.Sdk.Vcf.SddcManager is a module prerequisite and
    is provided by the execution environment.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [uri] $BaseUri,

        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [securestring] $AccessToken,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $SddcLcmFqdn,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $SddcManagerFqdn,

        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [pscredential] $SddcManagerCredential,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $SddcManagerSslThumbprint,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $FleetLcmFqdn,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $FleetLcmSslThumbprint,

        [Parameter()]
        [pscredential] $FleetLcmCredential,

        [Parameter()]
        [securestring] $FleetOpsToken,

        [Parameter()]
        [ValidateRange(0, 300)]
        [int] $PollIntervalSeconds = 2,

        [Parameter()]
        [ValidateRange(1, 86400)]
        [int] $TimeoutSeconds = 900
    )

    throw [System.NotImplementedException]::new(
        'Implement the setConfig request and getTask polling workflow.'
    )
}

Export-ModuleMember -Function Set-VcfSddcLcmConfiguration

