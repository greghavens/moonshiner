Set-StrictMode -Version Latest

<#
.SYNOPSIS
    Onboards a vCenter data source into VCF Operations for Networks behind a
    mandatory validation precheck.

.DESCRIPTION
    Drives the /api/ni operations named by docs/contract.json, which is a
    focused projection of
    specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml at
    commit c3f3b52c845dd967cabbc21680e893292077d5ba of the Apache-2.0
    vmware/vcf-api-specs repository.

    The mutating addVcenterDatasource operation must never run unless the
    validateVCenter precheck has succeeded.
#>
function Add-VcfNetworksVcenterDataSource {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        # Base URL of the VCF Operations for Networks appliance, without the
        # contract basePath, for example https://vrni.example.com
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Server,

        # Credential for the platform API user.
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [pscredential] $Credential,

        # Optional LDAP domain for the platform API user.
        [Parameter()]
        [string] $AuthDomain,

        # Name of the collector (PROXY_VM) node that must register the vCenter.
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $CollectorName,

        # Hostname of the vCenter. Mutually exclusive with -VcenterIp.
        [Parameter()]
        [string] $VcenterFqdn,

        # IP address of the vCenter. Mutually exclusive with -VcenterFqdn.
        [Parameter()]
        [string] $VcenterIp,

        # Credential the collector uses against the vCenter.
        [Parameter(Mandatory)]
        [ValidateNotNull()]
        [pscredential] $VcenterCredential,

        # Friendly nickname for the data source.
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Nickname,

        # Optional free-text notes for the data source.
        [Parameter()]
        [string] $Notes,

        # Register the data source with collection disabled.
        [Parameter()]
        [switch] $Disabled
    )

    throw [System.NotImplementedException]::new('Add-VcfNetworksVcenterDataSource is not implemented yet.')
}

Export-ModuleMember -Function 'Add-VcfNetworksVcenterDataSource'
