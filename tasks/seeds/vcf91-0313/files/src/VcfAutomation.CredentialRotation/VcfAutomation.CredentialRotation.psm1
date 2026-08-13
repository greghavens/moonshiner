#Requires -Version 7.2
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

<#
    VcfAutomation.CredentialRotation

    Drives the VCF Automation IaaS API described by docs/contract.json.

    Both public functions below are unimplemented. Implement them so that the
    wire traffic matches the contract exactly; see README.md for the required
    behaviour and docs/contract.json for the authority on every field.
#>

function New-VcfaUpdateCloudAccountSpecification {
    <#
    .SYNOPSIS
        Builds an UpdateCloudAccountSpecification model.

    .DESCRIPTION
        Returns an object carrying a ToJson() method that serialises the model
        the way the VMware.Sdk.Vcf model builders do: an optional property the
        caller did not supply does not appear in the output at all.

    .OUTPUTS
        An object with a ToJson() method returning a JSON string.
    #>
    [CmdletBinding()]
    [OutputType([object])]
    param(
        # Required by the contract.
        [Parameter(Mandatory)]
        [string] $Name,

        [Parameter(Mandatory)]
        [hashtable] $CloudAccountProperties,

        # Each entry must serialise to exactly { externalRegionId, name }.
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [object[]] $Regions,

        # Optional by the contract. Anything left unbound must be omitted
        # from the serialised body entirely.
        [Parameter()] [string]     $Description,
        [Parameter()] [string]     $PrivateKeyId,
        [Parameter()] [string]     $PrivateKey,
        [Parameter()] [hashtable]  $CustomProperties,
        [Parameter()] [string[]]   $AssociatedCloudAccountIds,
        [Parameter()] [hashtable]  $AssociatedMobilityCloudAccountIds,
        [Parameter()] [bool]       $CreateDefaultZones,
        [Parameter()] [object[]]   $Tags,
        [Parameter()] [hashtable]  $CertificateInfo
    )

    throw [System.NotImplementedException]::new(
        'New-VcfaUpdateCloudAccountSpecification is not implemented.')
}

function Invoke-VcfaCloudAccountCredentialRotation {
    <#
    .SYNOPSIS
        Rotates a VCF Automation cloud account's credentials without stranding
        in-flight requests on the old secret.

    .OUTPUTS
        A [pscustomobject] with the properties:
            DrainedRequestIds     [string[]] trackers waited on before rotating
            RotationRequestId     [string]
            RotationStatus        [string]   FINISHED or FAILED
            HealthCheckRequestId  [string]
            HealthCheckStatus     [string]   FINISHED or FAILED
            Succeeded             [bool]     both statuses are FINISHED
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        # Base URL of the VCF Automation endpoint, e.g. https://vcfa.corp.example.net
        [Parameter(Mandatory)]
        [string] $Server,

        # Bearer token obtained out of band. Not the cloud account secret.
        [Parameter(Mandatory)]
        [string] $AccessToken,

        [Parameter(Mandatory)]
        [string] $CloudAccountId,

        [Parameter(Mandatory)]
        [string] $NewPrivateKeyId,

        [Parameter(Mandatory)]
        [string] $NewPrivateKey,

        [Parameter()]
        [int] $DrainTimeoutSeconds = 60,

        [Parameter()]
        [int] $PollIntervalMilliseconds = 50
    )

    throw [System.NotImplementedException]::new(
        'Invoke-VcfaCloudAccountCredentialRotation is not implemented.')
}

Export-ModuleMember -Function @(
    'New-VcfaUpdateCloudAccountSpecification',
    'Invoke-VcfaCloudAccountCredentialRotation'
)
