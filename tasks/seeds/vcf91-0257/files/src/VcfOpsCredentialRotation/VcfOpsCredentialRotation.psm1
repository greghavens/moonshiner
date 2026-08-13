#Requires -Version 7.2

Set-StrictMode -Version Latest

<#
.SYNOPSIS
    Rotate the secret behind a VCF Operations credential without stranding
    adapter instances that are still collecting on the old one.

.DESCRIPTION
    NOT IMPLEMENTED.

    See README.md for the required behaviour and docs/contract.json for the
    operations this module is allowed to use.

.OUTPUTS
    [pscustomobject] with the properties:
        OldCredentialId      [string]
        NewCredentialId      [string]
        RepointedAdapterIds  [string[]]  ascending
        Drained              [bool]
        Retired              [bool]
        RetiredCredentialName[string]
#>
function Invoke-VcfOpsCredentialRotation {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Server,
        [Parameter(Mandatory)] [int]    $Port,
        [Parameter(Mandatory)] [string] $Protocol,
        [Parameter(Mandatory)] [string] $User,
        [Parameter(Mandatory)] [string] $Password,
        [Parameter(Mandatory)] [string] $AdapterKind,
        [Parameter(Mandatory)] [string] $CredentialName,
        [Parameter(Mandatory)] [string] $NewCredentialName,
        [Parameter(Mandatory)] [string] $NewSecret,
        [Parameter(Mandatory)] [string] $SecretFieldName,
        [int] $MaxAttempts = 3
    )

    throw [System.NotImplementedException]::new(
        'Invoke-VcfOpsCredentialRotation is not implemented yet.')
}

Export-ModuleMember -Function Invoke-VcfOpsCredentialRotation
