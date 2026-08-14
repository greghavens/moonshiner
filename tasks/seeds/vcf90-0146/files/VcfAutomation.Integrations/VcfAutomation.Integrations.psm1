Set-StrictMode -Version Latest

function New-VcfAutomationIntegration {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $Server,

        [Parameter(Mandatory)]
        [string] $AccessToken,

        [Parameter(Mandatory)]
        [string] $ApiVersion,

        [Parameter(Mandatory)]
        [string] $Name,

        [Parameter(Mandatory)]
        [string] $IntegrationType,

        [Parameter(Mandatory)]
        [System.Collections.IDictionary] $IntegrationProperties,

        [string] $Description,

        [bool] $ValidateOnly,

        [int] $PollIntervalMilliseconds = 1000
    )

    throw 'New-VcfAutomationIntegration is not implemented.'
}

Export-ModuleMember -Function New-VcfAutomationIntegration
