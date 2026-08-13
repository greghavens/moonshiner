#Requires -Version 7.0
<#
    VcfCredentialRotation - rotate SDDC Manager managed credentials on
    VMware Cloud Foundation 9.0.

    Nothing here is implemented yet. The exported surface below is the shape
    the acceptance harness drives; fill it in against docs/contract.json.
#>
Set-StrictMode -Version Latest

function Invoke-VcfCredentialRotation {
    [CmdletBinding()]
    param(
        # Base URL of the SDDC Manager appliance, for example https://sddc.vcf.local
        [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$Server,

        # SDDC Manager API user used to mint the initial token pair.
        [Parameter(Mandatory)][ValidateNotNull()][pscredential]$Credential,

        # Resource type whose credentials are in scope, for example ESXI.
        [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$ResourceType,

        # Credential type to rotate, for example SSH.
        [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$CredentialType,

        # Page size used while listing credentials.
        [ValidateRange(1, 1000)][int]$PageSize = 2,

        # Maximum number of polls per rotation task.
        [ValidateRange(1, 1000)][int]$MaxPolls = 30,

        # Invoked with the delay in seconds between non-terminal polls.
        [scriptblock]$SleepAction
    )

    throw [System.NotImplementedException]::new(
        'Invoke-VcfCredentialRotation has not been implemented yet.')
}

Export-ModuleMember -Function 'Invoke-VcfCredentialRotation'
