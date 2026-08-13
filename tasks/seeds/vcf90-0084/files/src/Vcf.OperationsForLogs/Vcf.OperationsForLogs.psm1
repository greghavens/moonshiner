Set-StrictMode -Version Latest

function Set-VcfOpsLogNotificationWebhook {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [uri] $Server,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string] $SessionId,

        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string[]] $Urls,

        [string] $ProxyId,

        [ValidateSet('pagerduty', 'slack', 'vro', 'custom')]
        [string] $DestinationApp,

        [ValidateSet('json', 'xml')]
        [string] $ContentType,

        [string] $Payload,

        [string] $Name,

        [string] $WebhookHeaders,

        [bool] $AcceptCert,

        [bool] $SendIndividualLogs
    )

    throw 'Set-VcfOpsLogNotificationWebhook is not implemented.'
}

Export-ModuleMember -Function Set-VcfOpsLogNotificationWebhook
