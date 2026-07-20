[CmdletBinding()]
param()

Set-StrictMode -Version Latest

Get-AzureADUser -All $true -ErrorAction Stop |
    Select-Object ObjectId, DisplayName, UserPrincipalName, AccountEnabled
