Set-StrictMode -Version Latest

<#
.SYNOPSIS
    Onboards a VMware Cloud Foundation Operations adapter instance behind a connection precheck.

.DESCRIPTION
    Runs the `testConnection` precheck first and only issues the mutating
    `createAdapterInstance` call when the precheck succeeds, so a failed precheck
    leaves the target unchanged.

    Implement this function using the VMware.Sdk.Vcf.Ops cmdlets supplied by the
    environment. Do not hand-roll HTTP requests and do not vendor the SDK.

.OUTPUTS
    [pscustomobject] with the properties Status, PrecheckPassed, AdapterInstanceId
    and Message.
#>
function Register-VcfOpsAdapterInstance {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Server,

        [Parameter(Mandatory)]
        [int] $Port,

        [Parameter(Mandatory)]
        [pscredential] $Credential,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $Name,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $AdapterKindKey,

        [ValidateSet('http', 'https')]
        [string] $Protocol = 'https',

        [ValidateNotNullOrEmpty()]
        [string] $AuthSource = 'local',

        [System.Collections.Specialized.OrderedDictionary] $ResourceIdentifier,

        [string] $Description,

        [string] $CollectorId,

        [Nullable[int]] $MonitoringInterval,

        [switch] $SkipCertificateCheck
    )

    throw [System.NotImplementedException]::new(
        'Register-VcfOpsAdapterInstance has not been implemented yet.')
}

Export-ModuleMember -Function 'Register-VcfOpsAdapterInstance'
